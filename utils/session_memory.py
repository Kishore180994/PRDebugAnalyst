"""
Persistent Session Memory for PR Debug Analyst.

When the agent's conversation history is trimmed to preserve context window,
this module maintains a compact running log of everything that happened:
- Commands run and their outcomes
- Files read and their contents/significance
- Edits applied to build files
- Errors encountered
- Key observations and findings

This memory is injected into agent prompts so the agent always has full context
of what's already been done, even after history trimming.
"""
from dataclasses import dataclass, field
from typing import List
from datetime import datetime


@dataclass
class CommandRecord:
    """Record of a command suggested/run."""
    command: str
    output_summary: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class FileReadRecord:
    """Record of a file read."""
    filepath: str
    summary: str  # one-line summary or key content
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class FileEditRecord:
    """Record of a file edit applied."""
    filepath: str
    change_description: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ErrorRecord:
    """Record of an error encountered."""
    error_description: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ObservationRecord:
    """Record of a key observation or finding."""
    text: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class SessionMemory:
    """
    Maintains a compact, persistent record of all session actions.

    This is injected into agent prompts to prevent context loss when
    conversation history is trimmed.
    """

    def __init__(self):
        self.commands: List[CommandRecord] = []
        self.files_read: List[FileReadRecord] = []
        self.files_edited: List[FileEditRecord] = []
        self.errors: List[ErrorRecord] = []
        self.observations: List[ObservationRecord] = []
        self.build_verified: bool = False

    def add_command(self, command: str, output_summary: str) -> None:
        """Record a command suggested/run and its outcome."""
        self.commands.append(CommandRecord(
            command=command,
            output_summary=output_summary,
        ))

    def add_file_read(self, filepath: str, summary: str) -> None:
        """Record a file read with a one-line summary."""
        # Truncate summary to ~100 chars if too long
        if len(summary) > 100:
            summary = summary[:97] + "..."
        self.files_read.append(FileReadRecord(
            filepath=filepath,
            summary=summary,
        ))

    def add_file_edit(self, filepath: str, change_description: str) -> None:
        """Record an edit applied to a build file."""
        self.files_edited.append(FileEditRecord(
            filepath=filepath,
            change_description=change_description,
        ))

    def add_error(self, error_description: str) -> None:
        """Record an error encountered."""
        self.errors.append(ErrorRecord(
            error_description=error_description,
        ))

    def add_observation(self, text: str) -> None:
        """Record a key observation or finding."""
        self.observations.append(ObservationRecord(
            text=text,
        ))

    def set_build_verified(self, verified: bool) -> None:
        """Set whether a build has been verified."""
        self.build_verified = verified

    def get_context_block(self, max_lines: int = 60) -> str:
        """
        Return a compact multi-line summary suitable for injection into agent prompts.

        Keeps the summary under max_lines to avoid bloating the prompt.
        Format is human-readable and easy to parse.
        """
        lines = []
        lines.append("=== SESSION CONTEXT (do not re-do completed steps) ===")
        lines.append("")

        # Commands run
        if self.commands:
            lines.append("Commands run:")
            for i, cmd_rec in enumerate(self.commands[-10:], 1):  # Last 10 commands only
                cmd_short = cmd_rec.command[:60]
                if len(cmd_rec.command) > 60:
                    cmd_short += "..."
                lines.append(f"  {i}. {cmd_short}")
                if cmd_rec.output_summary:
                    lines.append(f"     → {cmd_rec.output_summary[:50]}")
            lines.append("")

        # Files read
        if self.files_read:
            lines.append("Files read:")
            for file_rec in self.files_read[-8:]:  # Last 8 files only
                lines.append(f"  - {file_rec.filepath}")
                if file_rec.summary:
                    lines.append(f"    {file_rec.summary}")
            lines.append("")

        # Files edited
        if self.files_edited:
            lines.append("Edits applied:")
            for edit_rec in self.files_edited:
                lines.append(f"  - {edit_rec.filepath}: {edit_rec.change_description}")
            lines.append("")

        # Errors encountered
        if self.errors:
            lines.append("Errors encountered:")
            for err_rec in self.errors[-5:]:  # Last 5 errors only
                lines.append(f"  - {err_rec.error_description}")
            lines.append("")

        # Key observations
        if self.observations:
            lines.append("Key observations:")
            for obs_rec in self.observations[-5:]:  # Last 5 observations only
                lines.append(f"  - {obs_rec.text}")
            lines.append("")

        # Build status
        build_status = "Yes" if self.build_verified else "No"
        lines.append(f"Build verified: {build_status}")
        lines.append("")

        # Truncate to max_lines if necessary
        result = "\n".join(lines)
        result_lines = result.split("\n")
        if len(result_lines) > max_lines:
            result_lines = result_lines[:max_lines-1]
            result_lines.append("... (context trimmed)")

        return "\n".join(result_lines)

    def clear(self) -> None:
        """Reset memory (for testing or new sessions)."""
        self.commands = []
        self.files_read = []
        self.files_edited = []
        self.errors = []
        self.observations = []
        self.build_verified = False

    def to_dict(self) -> dict:
        """
        Convert memory to a dict for serialization/logging.
        Useful for debugging or saving session state.
        """
        return {
            "commands": [
                {"command": c.command, "output_summary": c.output_summary}
                for c in self.commands
            ],
            "files_read": [
                {"filepath": f.filepath, "summary": f.summary}
                for f in self.files_read
            ],
            "files_edited": [
                {"filepath": e.filepath, "change_description": e.change_description}
                for e in self.files_edited
            ],
            "errors": [
                {"error_description": e.error_description}
                for e in self.errors
            ],
            "observations": [
                {"text": o.text}
                for o in self.observations
            ],
            "build_verified": self.build_verified,
        }
