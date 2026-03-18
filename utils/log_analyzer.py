"""
Log analysis utilities for PR Debug Analyst.
Handles reading historical logs, polling terminal output, and log parsing.

The Tasks folder can contain a huge dump of nested folders and log files.
We use a two-phase approach to find the right logs for a specific PR:
  Phase 1 (Fast scan): Walk the tree, read file headers/names, build a candidate list
  Phase 2 (AI filter): Send candidate metadata to the denoiser model to confirm matches
"""
import os
import re
import glob
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from agents.gemini_client import GeminiClient


@dataclass
class BuildFailure:
    """Represents a parsed build failure from historical logs."""
    pr_id: str
    log_file: str
    error_type: str
    error_summary: str
    failed_tasks: list[str]
    root_cause_hint: str
    raw_snippet: str  # relevant portion of the log


class LogAnalyzer:
    """Reads and analyzes build logs from the Tasks folder and terminal output."""

    # Common Gradle/Android build error patterns
    ERROR_PATTERNS = [
        (r"FAILURE: Build failed with an exception", "BUILD_EXCEPTION"),
        (r"Could not resolve (?:all )?(?:dependencies|artifacts)", "DEPENDENCY_RESOLUTION"),
        (r"Execution failed for task '([^']+)'", "TASK_FAILURE"),
        (r"error: cannot find symbol", "COMPILATION_ERROR"),
        (r"error: incompatible types", "TYPE_ERROR"),
        (r"Manifest merger failed", "MANIFEST_MERGE"),
        (r"AAPT: error:", "AAPT_ERROR"),
        (r"Deprecated Gradle features were used", "DEPRECATION_WARNING"),
        (r"Out[Oo]f[Mm]emory|Java heap space|GC overhead", "OOM_ERROR"),
        (r"Could not determine the dependencies of task", "TASK_DEPENDENCY_ERROR"),
        (r"Plugin .* was not found", "PLUGIN_NOT_FOUND"),
        (r"SDK location not found", "SDK_NOT_FOUND"),
        (r"NDK not configured", "NDK_ERROR"),
        (r"Lint found errors", "LINT_ERROR"),
        (r"Test.*failed", "TEST_FAILURE"),
        (r"Kotlin.*error|Unresolved reference", "KOTLIN_ERROR"),
        (r"Duplicate class", "DUPLICATE_CLASS"),
        (r"minSdk.*mismatch|compileSdk", "SDK_VERSION_ERROR"),
    ]

    TASK_PATTERN = re.compile(r"> Task (:\S+)\s+(FAILED|UP-TO-DATE|FROM-CACHE|NO-SOURCE)")

    # Patterns to find PR links/references inside file content
    PR_LINK_PATTERNS = [
        # Full GitHub/GitLab PR URLs
        re.compile(r"https?://[^\s]+/pull/(\d+)"),
        re.compile(r"https?://[^\s]+/merge_requests/(\d+)"),
        re.compile(r"https?://[^\s]+/pull-requests/(\d+)"),  # Bitbucket
        # Short references
        re.compile(r"PR[#_-]?\s*(\d+)", re.IGNORECASE),
        re.compile(r"pull\s*request\s*#?\s*(\d+)", re.IGNORECASE),
        re.compile(r"MR[#_-]?\s*(\d+)", re.IGNORECASE),  # merge request
    ]

    def __init__(self, tasks_folder: str):
        self.tasks_folder = tasks_folder

    # ══════════════════════════════════════════════════════════════════════
    #  Phase 1: Fast scan — build candidate list from the dump
    # ══════════════════════════════════════════════════════════════════════

    def _discover_all_files(self) -> list[str]:
        """
        Walk the entire tasks folder tree and return all file paths.
        Includes any file type — the AI will decide relevance.
        """
        all_files = []
        for root, dirs, files in os.walk(self.tasks_folder):
            # Skip hidden directories and common junk
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
            for fname in files:
                if fname.startswith("."):
                    continue
                full_path = os.path.join(root, fname)
                try:
                    size = os.path.getsize(full_path)
                    if size > 0 and size < 100_000_000:  # skip empty & >100MB
                        all_files.append(full_path)
                except OSError:
                    continue
        return all_files

    def _extract_pr_references(self, filepath: str, head_content: str) -> list[str]:
        """
        Extract all PR links/references from a file's name and head content.
        Returns a list of normalized PR identifiers found.
        """
        refs = set()

        # Check filename and parent directory names
        path_str = filepath.lower()
        for pattern in self.PR_LINK_PATTERNS:
            for m in pattern.finditer(path_str):
                refs.add(m.group(0))
            for m in pattern.finditer(filepath):  # case-sensitive pass for URLs
                refs.add(m.group(0))

        # Check head content
        for pattern in self.PR_LINK_PATTERNS:
            for m in pattern.finditer(head_content):
                refs.add(m.group(0))

        return list(refs)

    def _build_file_index(self) -> list[dict]:
        """
        Scan all files in the tasks folder and build a lightweight index.
        Each entry has: path, filename, size, head (first 2KB), pr_refs found.
        """
        all_files = self._discover_all_files()
        index = []

        for fpath in all_files:
            try:
                with open(fpath, "r", errors="replace") as f:
                    head = f.read(2048)
            except (PermissionError, OSError, UnicodeDecodeError):
                head = ""

            # Skip binary files (heuristic: too many null bytes)
            if "\x00" in head[:512]:
                continue

            pr_refs = self._extract_pr_references(fpath, head)
            rel_path = os.path.relpath(fpath, self.tasks_folder)

            index.append({
                "path": fpath,
                "rel_path": rel_path,
                "filename": os.path.basename(fpath),
                "dir": os.path.basename(os.path.dirname(fpath)),
                "size": os.path.getsize(fpath),
                "head_preview": head[:500],  # compact preview
                "pr_refs": pr_refs,
            })

        return index

    # ══════════════════════════════════════════════════════════════════════
    #  Phase 2: AI-powered filtering — find the right logs for a PR
    # ══════════════════════════════════════════════════════════════════════

    def find_logs_for_pr(self, pr_link: str, gemini_client: "GeminiClient") -> list[dict]:
        """
        Main entry point: find all log files relevant to a specific PR.

        Strategy:
        1. Fast scan: build file index with PR references
        2. Quick filter: narrow down by exact PR link/number match
        3. AI filter: if ambiguous, use Gemini to confirm relevance
        4. Full analysis: deeply analyze the confirmed log files
        """
        from utils.display import info, warning, progress_spinner, progress_done

        # Step 1: Index all files
        progress_spinner("Indexing tasks folder")
        file_index = self._build_file_index()
        progress_done()
        info(f"Found {len(file_index)} files in tasks folder")

        if not file_index:
            warning("Tasks folder is empty or contains no readable files.")
            return []

        # Step 2: Extract PR identifiers from the user's link
        pr_identifiers = self._normalize_pr_link(pr_link)
        info(f"Searching for PR identifiers: {pr_identifiers}")

        # Step 3: Fast filter — exact matches on PR references
        exact_matches = []
        possible_matches = []

        for entry in file_index:
            match_score = self._score_pr_match(entry, pr_identifiers)
            if match_score >= 2:
                exact_matches.append(entry)
            elif match_score >= 1:
                possible_matches.append(entry)

        info(f"Fast filter: {len(exact_matches)} exact matches, {len(possible_matches)} possible matches")

        # Step 4: If we have exact matches, optionally verify with AI
        # If we only have possibles or too many, use AI to filter
        candidates = exact_matches.copy()

        if not exact_matches and possible_matches:
            # No exact matches — use AI to identify the right files
            progress_spinner("AI scanning possible matches")
            ai_confirmed = self._ai_filter_candidates(
                possible_matches, pr_link, pr_identifiers, gemini_client
            )
            progress_done()
            candidates = ai_confirmed

        elif not exact_matches and not possible_matches:
            # No matches at all — AI broad scan as last resort
            progress_spinner("AI performing broad scan (no direct matches found)")
            ai_confirmed = self._ai_broad_scan(file_index, pr_link, gemini_client)
            progress_done()
            candidates = ai_confirmed

        if not candidates:
            warning("No log files found matching this PR.")
            return []

        info(f"Confirmed {len(candidates)} relevant log file(s)")

        # Step 5: Deep analysis of confirmed files (cap to avoid hanging)
        MAX_ANALYZE = 15  # Don't analyze more than 15 files deeply
        if len(candidates) > MAX_ANALYZE:
            warning(f"Too many matches ({len(candidates)}). Analyzing top {MAX_ANALYZE} by size relevance.")
            # Sort by size (smaller logs are often more focused/useful)
            candidates.sort(key=lambda e: e.get("size", 0))
            candidates = candidates[:MAX_ANALYZE]

        progress_spinner(f"Analyzing {len(candidates)} log file(s)")
        results = []
        for i, entry in enumerate(candidates):
            try:
                analysis = self._analyze_log_file(entry["path"])
                if analysis:
                    analysis["match_source"] = "exact" if entry in exact_matches else "ai_confirmed"
                    results.append(analysis)
            except Exception as e:
                warning(f"Skipping {entry['filename']}: {e}")
        progress_done(f"{len(results)} analyzed")

        return results

    def _normalize_pr_link(self, pr_link: str) -> list[str]:
        """
        Extract all searchable identifiers from a PR link.
        E.g., 'https://github.com/user/repo/pull/1234' → ['1234', 'pull/1234', 'github.com/user/repo/pull/1234']
        """
        identifiers = [pr_link]  # Always include the full link

        # Extract PR number
        pr_num_patterns = [
            r"/pull/(\d+)",
            r"/merge_requests/(\d+)",
            r"/pull-requests/(\d+)",
            r"#(\d+)$",
            r"PR[_-]?(\d+)",
        ]
        for p in pr_num_patterns:
            m = re.search(p, pr_link, re.IGNORECASE)
            if m:
                num = m.group(1)
                identifiers.extend([
                    num,
                    f"#{num}",
                    f"PR-{num}",
                    f"PR_{num}",
                    f"PR#{num}",
                    f"pull/{num}",
                    f"merge_requests/{num}",
                ])
                break

        # Extract repo name if it's a full URL
        repo_match = re.search(r"github\.com/([^/]+/[^/]+)", pr_link)
        if not repo_match:
            repo_match = re.search(r"gitlab\.com/([^/]+/[^/]+)", pr_link)
        if not repo_match:
            repo_match = re.search(r"bitbucket\.org/([^/]+/[^/]+)", pr_link)
        if repo_match:
            identifiers.append(repo_match.group(1))

        return list(set(identifiers))

    def _score_pr_match(self, file_entry: dict, pr_identifiers: list[str]) -> int:
        """
        Score how likely a file is related to the target PR.
        0 = no match, 1 = possible, 2+ = strong match.
        """
        score = 0
        search_text = (
            file_entry["rel_path"].lower() + "\n" +
            file_entry.get("head_preview", "").lower() + "\n" +
            " ".join(file_entry.get("pr_refs", [])).lower()
        )

        for identifier in pr_identifiers:
            ident_lower = identifier.lower()

            # Full URL match in content → strong
            if ident_lower.startswith("http") and ident_lower in search_text:
                score += 3

            # PR number match in refs → strong
            elif ident_lower in search_text:
                score += 1

            # Check in filename/directory specifically (stronger signal)
            if ident_lower in file_entry["rel_path"].lower():
                score += 2

        return score

    def _ai_filter_candidates(
        self,
        candidates: list[dict],
        pr_link: str,
        pr_identifiers: list[str],
        gemini_client: "GeminiClient",
    ) -> list[dict]:
        """
        Use the fast AI model to determine which candidate files are truly
        related to the target PR. Sends compact metadata, not full file content.
        """
        # Build compact listing for the AI
        listing = []
        for i, c in enumerate(candidates[:50]):  # cap at 50 candidates
            listing.append(
                f"[{i}] {c['rel_path']}  (size: {c['size']}B)\n"
                f"    PR refs found: {c['pr_refs']}\n"
                f"    Preview: {c['head_preview'][:200]}"
            )

        prompt = f"""You are helping find build log files for a specific PR in a large dump folder.

TARGET PR: {pr_link}
PR IDENTIFIERS: {pr_identifiers}

Below are candidate files that MIGHT contain logs for this PR.
For each file, decide if it is RELEVANT (contains build logs for this specific PR) or NOT.

CANDIDATES:
{chr(10).join(listing)}

Respond with ONLY the indices of relevant files, comma-separated. Example: 0,3,7
If none are relevant, respond with: NONE
Do NOT explain — just output the indices or NONE."""

        try:
            response = gemini_client.denoise_logs.__func__  # we need a raw call
            # Use the denoiser model directly for speed
            from google.genai import types
            result = gemini_client.client.models.generate_content(
                model=gemini_client.config.denoiser_model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=256),
            )
            text = (result.text or "").strip()

            if text.upper() == "NONE":
                return []

            # Parse indices
            indices = []
            for part in re.findall(r"\d+", text):
                idx = int(part)
                if 0 <= idx < len(candidates):
                    indices.append(idx)

            return [candidates[i] for i in indices]

        except Exception as e:
            # Fallback: return all candidates if AI fails
            from utils.display import warning
            warning(f"AI filter failed ({e}), using all candidates")
            return candidates

    def _ai_broad_scan(
        self,
        file_index: list[dict],
        pr_link: str,
        gemini_client: "GeminiClient",
    ) -> list[dict]:
        """
        Last resort: when no fast matches found, send the entire folder structure
        to AI and ask it to identify which files might be relevant.
        """
        # Build a compact tree view
        tree_lines = []
        for i, entry in enumerate(file_index[:200]):  # cap at 200
            tree_lines.append(
                f"[{i}] {entry['rel_path']}  ({entry['size']}B)  refs={entry['pr_refs']}"
            )

        prompt = f"""You are searching a large dump folder for build log files related to a specific PR.
No direct PR reference was found, so we need your help identifying candidate files.

TARGET PR: {pr_link}

Here is the folder structure (index, path, size, any PR refs found):
{chr(10).join(tree_lines)}

Consider:
- Files might be organized by date, PR number, branch name, or build ID
- The PR link components (repo name, PR number) might appear in folder names
- Build logs typically have extensions like .log, .txt, .output, or no extension
- Look for patterns like timestamps near the PR creation time
- The folder/file naming might encode the PR info in a non-obvious way

Respond with ONLY the indices of the most likely candidates (up to 10), comma-separated.
If you truly can't identify any candidates, respond: NONE"""

        try:
            from google.genai import types
            result = gemini_client.client.models.generate_content(
                model=gemini_client.config.denoiser_model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=256),
            )
            text = (result.text or "").strip()

            if text.upper() == "NONE":
                return []

            indices = []
            for part in re.findall(r"\d+", text):
                idx = int(part)
                if 0 <= idx < len(file_index):
                    indices.append(idx)

            return [file_index[i] for i in indices]

        except Exception as e:
            from utils.display import warning
            warning(f"AI broad scan failed ({e})")
            return []

    # ══════════════════════════════════════════════════════════════════════
    #  Legacy: scan all (still useful for overview)
    # ══════════════════════════════════════════════════════════════════════

    def scan_tasks_folder(self) -> list[dict]:
        """
        Scan the tasks folder for ALL log files and return a summary of each.
        Use find_logs_for_pr() instead for PR-specific discovery.
        """
        log_files = []
        extensions = ("*.log", "*.txt", "*.build_log", "*.output")

        for ext in extensions:
            pattern = os.path.join(self.tasks_folder, "**", ext)
            log_files.extend(glob.glob(pattern, recursive=True))

        for f in glob.glob(os.path.join(self.tasks_folder, "**", "*"), recursive=True):
            if os.path.isfile(f) and not any(f.endswith(e.replace("*", "")) for e in extensions):
                try:
                    with open(f, "r", errors="replace") as fh:
                        head = fh.read(1024)
                        if any(kw in head.lower() for kw in ["gradle", "build", "error", "task", "android"]):
                            log_files.append(f)
                except (PermissionError, OSError):
                    continue

        results = []
        for lf in sorted(set(log_files)):
            info = self._analyze_log_file(lf)
            if info:
                results.append(info)

        return results

    # ══════════════════════════════════════════════════════════════════════
    #  Deep analysis of individual log files
    # ══════════════════════════════════════════════════════════════════════

    def _analyze_log_file(self, filepath: str, max_size: int = 5_000_000) -> Optional[dict]:
        """Analyze a single log file and extract failure information."""
        try:
            file_size = os.path.getsize(filepath)
            with open(filepath, "r", errors="replace") as f:
                if file_size > max_size:
                    # For huge files, read head + tail
                    head = f.read(max_size // 2)
                    f.seek(max(0, file_size - max_size // 2))
                    tail = f.read()
                    content = head + "\n...[truncated]...\n" + tail
                else:
                    content = f.read()
        except (PermissionError, OSError):
            return None

        if not content.strip():
            return None

        # Extract PR ID from filename or content
        pr_id = self._extract_pr_id(filepath, content)

        # Find error types
        errors_found = []
        for pattern, error_type in self.ERROR_PATTERNS:
            matches = re.findall(pattern, content)
            if matches:
                errors_found.append({
                    "type": error_type,
                    "count": len(matches),
                    "first_match": matches[0] if isinstance(matches[0], str) else str(matches[0]),
                })

        # Extract failed tasks
        failed_tasks = []
        for match in self.TASK_PATTERN.finditer(content):
            if match.group(2) == "FAILED":
                failed_tasks.append(match.group(1))

        # Extract the error snippet (lines around FAILURE or first error)
        error_snippet = self._extract_error_snippet(content)

        # Extract all PR links found in the file (for cross-referencing)
        all_pr_refs = []
        for pat in self.PR_LINK_PATTERNS:
            all_pr_refs.extend(m.group(0) for m in pat.finditer(content[:5000]))

        return {
            "file": filepath,
            "filename": os.path.basename(filepath),
            "rel_path": os.path.relpath(filepath, self.tasks_folder) if self.tasks_folder else filepath,
            "pr_id": pr_id,
            "pr_refs_in_file": list(set(all_pr_refs)),
            "errors": errors_found,
            "failed_tasks": failed_tasks,
            "error_snippet": error_snippet,
            "file_size": os.path.getsize(filepath),
            "line_count": content.count("\n"),
        }

    def _extract_pr_id(self, filepath: str, content: str) -> str:
        """Try to extract a PR identifier from the filename or content."""
        basename = os.path.basename(filepath)

        patterns = [
            r"PR[_-]?(\d+)",
            r"pr[_-]?(\d+)",
            r"pull[_-]?(\d+)",
            r"#(\d+)",
            r"(\d{3,6})",
        ]
        for p in patterns:
            m = re.search(p, basename)
            if m:
                return f"PR-{m.group(1)}"

        for p in patterns[:3]:
            m = re.search(p, content[:2000])
            if m:
                return f"PR-{m.group(1)}"

        return os.path.splitext(basename)[0]

    def _extract_error_snippet(self, content: str, context_lines: int = 15) -> str:
        """Extract the most relevant error snippet from log content."""
        lines = content.split("\n")

        for i, line in enumerate(lines):
            if re.search(r"FAILURE:|BUILD FAILED|FATAL:", line, re.IGNORECASE):
                start = max(0, i - 5)
                end = min(len(lines), i + context_lines)
                return "\n".join(lines[start:end])

        for i, line in enumerate(lines):
            if re.search(r"\berror\b", line, re.IGNORECASE):
                start = max(0, i - 3)
                end = min(len(lines), i + context_lines)
                return "\n".join(lines[start:end])

        return "\n".join(lines[-20:])

    def get_full_log_content(self, filepath: str) -> str:
        """Read the full content of a log file for agent consumption."""
        try:
            with open(filepath, "r", errors="replace") as f:
                return f.read()
        except (PermissionError, OSError) as e:
            return f"[Error reading {filepath}: {e}]"

    # ══════════════════════════════════════════════════════════════════════
    #  Terminal Log Polling
    # ══════════════════════════════════════════════════════════════════════

    def read_terminal_logs(self, log_path: str, last_position: int = 0) -> tuple[str, int]:
        """Read new content from a terminal log file since last_position."""
        try:
            with open(log_path, "r", errors="replace") as f:
                f.seek(last_position)
                new_content = f.read()
                new_position = f.tell()
            return new_content, new_position
        except FileNotFoundError:
            return "", last_position
        except (PermissionError, OSError) as e:
            return f"[Error reading log: {e}]", last_position

    def read_latest_logs(self, log_path: str, tail_lines: int = 200) -> str:
        """Read the last N lines from a log file."""
        try:
            with open(log_path, "r", errors="replace") as f:
                lines = f.readlines()
                return "".join(lines[-tail_lines:])
        except (FileNotFoundError, PermissionError, OSError) as e:
            return f"[Error reading log: {e}]"

    # ══════════════════════════════════════════════════════════════════════
    #  Build File Detection
    # ══════════════════════════════════════════════════════════════════════

    def find_build_files(self, project_path: str) -> list[str]:
        """Find all build-related files in an Android project."""
        build_patterns = [
            "**/build.gradle",
            "**/build.gradle.kts",
            "**/settings.gradle",
            "**/settings.gradle.kts",
            "**/gradle.properties",
            "**/gradle/wrapper/gradle-wrapper.properties",
            "**/proguard-rules.pro",
            "**/proguard-rules.txt",
            "**/*proguard*.cfg",
            "**/AndroidManifest.xml",
            "**/libs.versions.toml",
            "**/version.catalog",
        ]

        found = []
        for pattern in build_patterns:
            found.extend(glob.glob(os.path.join(project_path, pattern), recursive=True))

        return sorted(set(found))

    def read_build_file(self, filepath: str) -> str:
        """Read a build file's content."""
        try:
            with open(filepath, "r", errors="replace") as f:
                return f.read()
        except (FileNotFoundError, PermissionError, OSError) as e:
            return f"[Error reading {filepath}: {e}]"
