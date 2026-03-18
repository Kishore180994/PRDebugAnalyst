"""
Session Report Generator for PR Debug Analyst.

Handles:
  - Parsing the structured SUMMARY block from the agent's final response
  - Generating a .sh fix script on success
  - Generating a human-readable failure report on failure
  - Tracking steps throughout the session for the final summary
"""
import os
import re
import datetime
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StepRecord:
    """A single step taken during the debugging session."""
    description: str
    command: str = ""
    result: str = ""            # "success", "failed", "skipped", etc.
    output_summary: str = ""    # compact summary of what the output showed


@dataclass
class SessionState:
    """
    Tracks the entire debugging session state.
    Populated incrementally as the session progresses,
    and used to generate the final report + fix script.
    """
    pr_link: str = ""
    mode: str = ""                              # "manual" or "auto"
    project_path: str = ""
    historical_errors: list[str] = field(default_factory=list)
    live_build_errors: list[str] = field(default_factory=list)
    logs_matched_historical: Optional[bool] = None
    steps: list[StepRecord] = field(default_factory=list)
    files_changed: list[dict] = field(default_factory=list)  # {"file": ..., "change": ...}
    fix_applied: str = ""
    root_cause: str = ""
    verdict: str = ""
    verdict_reason: str = ""
    why_unfixable: str = ""
    started_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    ended_at: str = ""

    def add_step(self, description: str, command: str = "", result: str = "", output_summary: str = ""):
        """Record a step taken during the session."""
        self.steps.append(StepRecord(
            description=description,
            command=command,
            result=result,
            output_summary=output_summary,
        ))

    def finalize(self, verdict: str, reason: str):
        """Mark the session as complete."""
        self.verdict = verdict
        self.verdict_reason = reason
        self.ended_at = datetime.datetime.now().isoformat()


class SummaryParser:
    """Parses the structured SUMMARY block from the agent's final response."""

    SUMMARY_PATTERN = re.compile(r"SUMMARY_START\s*\n(.*?)\nSUMMARY_END", re.DOTALL)

    @classmethod
    def parse(cls, response: str) -> Optional[dict]:
        """
        Extract and parse the SUMMARY block from the agent's response.
        Returns a dict with the parsed fields, or None if no summary found.
        """
        match = cls.SUMMARY_PATTERN.search(response)
        if not match:
            return None

        raw = match.group(1)
        result = {
            "status": "",
            "pr": "",
            "root_cause": "",
            "steps_tried": [],
            "fix_applied": "",
            "files_changed": [],
            "why_unfixable": "",
        }

        # Parse simple key: value fields
        for key in ("status", "pr", "root_cause", "fix_applied", "why_unfixable"):
            pattern = rf"^{key}:\s*(.+?)$"
            m = re.search(pattern, raw, re.MULTILINE)
            if m:
                result[key] = m.group(1).strip()

        # Parse steps_tried list — line-by-line approach for robustness
        result["steps_tried"] = cls._parse_list_section(raw, "steps_tried", ["step", "result"])
        # Parse files_changed list
        result["files_changed"] = cls._parse_list_section(raw, "files_changed", ["file", "change"])

        return result

    @classmethod
    def _parse_list_section(cls, raw: str, section_key: str, field_names: list[str]) -> list[dict]:
        """
        Parse a YAML-like list section from the summary block.
        E.g., for section_key="steps_tried" and field_names=["step", "result"]:
          steps_tried:
          - step: Did something
            result: It worked
          - step: Did another
            result: It failed
        Returns list of dicts with the given field names as keys.
        """
        lines = raw.split("\n")
        in_section = False
        items = []
        current_item = {}

        for line in lines:
            stripped = line.strip()

            # Detect start of our section
            if stripped.startswith(f"{section_key}:"):
                in_section = True
                continue

            if not in_section:
                continue

            # Detect end of section: a non-indented line that's a new top-level key
            # (not starting with - or whitespace, and contains a colon)
            if stripped and not stripped.startswith("-") and not line.startswith((" ", "\t")) and ":" in stripped:
                break

            # New list item
            if stripped.startswith("-"):
                if current_item:
                    items.append(current_item)
                current_item = {}
                # Parse the first field from the same line: "- step: value"
                after_dash = stripped[1:].strip()
                for fn in field_names:
                    if after_dash.startswith(f"{fn}:"):
                        current_item[fn] = after_dash[len(fn) + 1:].strip()
                        break

            # Continuation line within a list item
            elif stripped and current_item is not None:
                for fn in field_names:
                    if stripped.startswith(f"{fn}:"):
                        current_item[fn] = stripped[len(fn) + 1:].strip()
                        break

        # Don't forget the last item
        if current_item:
            items.append(current_item)

        # Ensure all items have all field names (default to "")
        for item in items:
            for fn in field_names:
                item.setdefault(fn, "")

        return items

    @classmethod
    def merge_with_session(cls, agent_summary: Optional[dict], session: SessionState) -> SessionState:
        """
        Merge the agent's parsed summary with the local session state.
        The agent summary may have better root_cause/why_unfixable analysis,
        while session state has accurate step tracking.
        """
        if agent_summary:
            if agent_summary.get("root_cause"):
                session.root_cause = agent_summary["root_cause"]
            if agent_summary.get("fix_applied") and agent_summary["fix_applied"] != "none":
                session.fix_applied = agent_summary["fix_applied"]
            if agent_summary.get("why_unfixable"):
                session.why_unfixable = agent_summary["why_unfixable"]
            if agent_summary.get("files_changed"):
                # Merge, preferring agent's list if we don't have one
                if not session.files_changed:
                    session.files_changed = agent_summary["files_changed"]
            # Merge steps — agent may have a more concise version
            if agent_summary.get("steps_tried") and not session.steps:
                for s in agent_summary["steps_tried"]:
                    session.add_step(s["step"], result=s["result"])

        return session


class ReportGenerator:
    """Generates final outputs: summary display, .sh fix script, JSON report."""

    @staticmethod
    def generate_fix_script(session: SessionState, output_dir: str) -> str:
        """
        Generate a .sh script that reproduces the fix for this PR.
        Only generated on BUILD_FIXED verdict.
        Returns the path to the generated script.
        """
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sanitize PR link for filename
        pr_num = re.search(r"(\d+)\s*$", session.pr_link)
        pr_tag = f"PR{pr_num.group(1)}" if pr_num else "fix"
        script_name = f"fix_{pr_tag}_{timestamp}.sh"
        script_path = os.path.join(output_dir, script_name)

        # Collect all commands that were part of the fix (not diagnostic commands)
        fix_commands = []
        diagnostic_commands = []

        for step in session.steps:
            if not step.command:
                continue
            # Classify: build/verify commands vs fix commands
            cmd = step.command.strip()
            is_diagnostic = any(kw in cmd.lower() for kw in [
                "gradlew", "gradle", "cat ", "grep ", "find ", "ls ",
                "echo ", "pwd", "--version", "--stacktrace",
            ])
            if is_diagnostic:
                diagnostic_commands.append((step.description, cmd))
            else:
                fix_commands.append((step.description, cmd))

        # Also include file changes
        file_edits = []
        for fc in session.files_changed:
            file_edits.append(fc)

        lines = [
            "#!/bin/bash",
            "#",
            f"# PR Debug Analyst - Auto-generated fix script",
            f"# PR: {session.pr_link}",
            f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"# Root Cause: {session.root_cause}",
            f"# Fix: {session.fix_applied}",
            "#",
            "# Usage:",
            "#   cd /path/to/your/android/project",
            f"#   bash {script_name}",
            "#",
            "",
            'set -euo pipefail',
            "",
            'echo "=========================================="',
            f'echo " PR Fix Script: {session.pr_link}"',
            f'echo " Root Cause: {session.root_cause}"',
            'echo "=========================================="',
            'echo ""',
            "",
            "# ── Step 1: Verify we're in the right directory ──",
            'if [ ! -f "build.gradle" ] && [ ! -f "build.gradle.kts" ] && [ ! -f "settings.gradle" ] && [ ! -f "settings.gradle.kts" ]; then',
            '    echo "ERROR: No gradle build files found in current directory."',
            '    echo "Please cd to your Android project root first."',
            '    exit 1',
            'fi',
            'echo "✓ Android project root detected"',
            'echo ""',
            "",
        ]

        # Step 2: Apply file edits
        if file_edits:
            lines.append("# ── Step 2: Apply build file fixes ──")
            for i, fc in enumerate(file_edits, 1):
                lines.append(f'echo "Applying fix {i}: {fc["change"]} in {fc["file"]}"')
                # We output sed commands if we can infer them, otherwise a comment
                lines.append(f'# Fix: {fc["change"]}')
                lines.append(f'# Target file: {fc["file"]}')
            lines.append("")

        # Step 2b: Include explicit fix commands
        if fix_commands:
            lines.append("# ── Step 2: Apply fixes ──")
            for desc, cmd in fix_commands:
                lines.append(f'echo "  → {desc}"')
                lines.append(cmd)
                lines.append("")

        # Step 3: Rebuild
        lines.extend([
            "# ── Step 3: Rebuild to verify fix ──",
            'echo ""',
            'echo "Rebuilding project to verify fix..."',
            'echo ""',
            "",
            "if [ -f \"gradlew\" ]; then",
            "    ./gradlew assembleDebug --stacktrace",
            "elif [ -f \"gradlew.bat\" ]; then",
            "    # Windows compatibility",
            "    ./gradlew.bat assembleDebug --stacktrace",
            "else",
            "    gradle assembleDebug --stacktrace",
            "fi",
            "",
            "BUILD_EXIT=$?",
            "",
            'if [ $BUILD_EXIT -eq 0 ]; then',
            '    echo ""',
            '    echo "=========================================="',
            '    echo " ✅ BUILD SUCCESSFUL - Fix verified!"',
            f'    echo " PR: {session.pr_link}"',
            '    echo "=========================================="',
            "else",
            '    echo ""',
            '    echo "=========================================="',
            '    echo " ❌ BUILD STILL FAILING"',
            '    echo " The automated fix did not resolve the issue."',
            '    echo " Manual investigation may be needed."',
            '    echo "=========================================="',
            "    exit 1",
            "fi",
        ])

        os.makedirs(output_dir, exist_ok=True)
        with open(script_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(script_path, 0o755)

        return script_path

    @staticmethod
    def generate_failure_summary(session: SessionState) -> str:
        """
        Generate a human-readable failure summary when the build can't be fixed.
        Displayed to the user at the end of a failed session.
        """
        lines = []
        lines.append("=" * 62)
        lines.append("  SESSION FAILURE SUMMARY")
        lines.append("=" * 62)
        lines.append("")
        lines.append(f"  PR:          {session.pr_link}")
        lines.append(f"  Verdict:     {session.verdict}")
        lines.append(f"  Root Cause:  {session.root_cause or 'Could not determine'}")
        lines.append("")

        if session.historical_errors:
            lines.append("  Historical errors found:")
            for err in session.historical_errors[:5]:
                lines.append(f"    • {err}")
            lines.append("")

        if session.live_build_errors:
            lines.append("  Live build errors:")
            for err in session.live_build_errors[:5]:
                lines.append(f"    • {err}")
            lines.append("")

        if session.logs_matched_historical is not None:
            match_str = "Yes" if session.logs_matched_historical else "No — different errors in live build"
            lines.append(f"  Live errors match historical: {match_str}")
            lines.append("")

        lines.append("  Steps attempted:")
        if session.steps:
            for i, step in enumerate(session.steps, 1):
                result_icon = "✓" if step.result == "success" else "✗" if step.result == "failed" else "→"
                lines.append(f"    {i}. [{result_icon}] {step.description}")
                if step.command:
                    lines.append(f"       cmd: {step.command[:80]}")
                if step.output_summary:
                    lines.append(f"       out: {step.output_summary[:100]}")
        else:
            lines.append("    (no steps recorded)")
        lines.append("")

        if session.why_unfixable:
            lines.append("  Why this cannot be fixed via build config:")
            # Word-wrap the reason
            words = session.why_unfixable.split()
            line = "    "
            for word in words:
                if len(line) + len(word) + 1 > 70:
                    lines.append(line)
                    line = "    "
                line += word + " "
            if line.strip():
                lines.append(line)
            lines.append("")

        if session.files_changed:
            lines.append("  Files that were modified (may need reverting):")
            for fc in session.files_changed:
                lines.append(f"    • {fc['file']}: {fc['change']}")
            lines.append("")

        lines.append("  Recommendation:")
        if session.verdict == "BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED":
            lines.append("    This PR requires source code changes to fix the build.")
            lines.append("    The PR author needs to update their code.")
            lines.append("    Consider leaving a review comment with the root cause above.")
        elif session.verdict == "BUILD_UNFIXABLE_UNKNOWN":
            lines.append("    The root cause could not be determined automatically.")
            lines.append("    Manual investigation by a developer is recommended.")
        elif session.verdict == "NEEDS_MORE_INVESTIGATION":
            lines.append("    More investigation is needed. Consider running with")
            lines.append("    more verbose logging (--debug flag) or checking CI environment.")
        lines.append("")
        lines.append("=" * 62)

        return "\n".join(lines)

    @staticmethod
    def generate_success_summary(session: SessionState) -> str:
        """
        Generate a human-readable success summary when the build is fixed.
        """
        lines = []
        lines.append("=" * 62)
        lines.append("  BUILD FIXED — SESSION SUMMARY")
        lines.append("=" * 62)
        lines.append("")
        lines.append(f"  PR:          {session.pr_link}")
        lines.append(f"  Root Cause:  {session.root_cause}")
        lines.append(f"  Fix Applied: {session.fix_applied}")
        lines.append("")

        if session.files_changed:
            lines.append("  Files modified:")
            for fc in session.files_changed:
                lines.append(f"    • {fc['file']}")
                lines.append(f"      Change: {fc['change']}")
            lines.append("")

        lines.append("  Steps taken:")
        for i, step in enumerate(session.steps, 1):
            result_icon = "✓" if step.result == "success" else "✗" if step.result == "failed" else "→"
            lines.append(f"    {i}. [{result_icon}] {step.description}")
        lines.append("")

        lines.append("=" * 62)
        return "\n".join(lines)

    @staticmethod
    def save_json_report(session: SessionState, output_dir: str) -> str:
        """Save the full session as a JSON report."""
        import json

        report = {
            "timestamp": session.started_at,
            "ended_at": session.ended_at,
            "pr_link": session.pr_link,
            "mode": session.mode,
            "verdict": session.verdict,
            "verdict_reason": session.verdict_reason,
            "root_cause": session.root_cause,
            "fix_applied": session.fix_applied,
            "why_unfixable": session.why_unfixable,
            "historical_errors": session.historical_errors,
            "live_build_errors": session.live_build_errors,
            "logs_matched_historical": session.logs_matched_historical,
            "steps": [
                {
                    "description": s.description,
                    "command": s.command,
                    "result": s.result,
                    "output_summary": s.output_summary,
                }
                for s in session.steps
            ],
            "files_changed": session.files_changed,
        }

        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = os.path.join(output_dir, f"session_{timestamp}.json")

        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        return report_path
