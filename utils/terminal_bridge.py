"""
Terminal Bridge - Handles communication between Terminal A (project) and Terminal B (agent).
Manages log file watching and command execution for both manual and auto modes.
"""
import os
import subprocess
import time
import tempfile
from pathlib import Path
from typing import Optional


class TerminalBridge:
    """
    Bridge between the agent (Terminal B) and the project terminal (Terminal A).

    In manual mode:
        - Displays commands for the user to run in Terminal A
        - Reads log output from a shared log file

    In auto mode:
        - Executes commands directly via subprocess
        - Captures output in real-time
    """

    def __init__(self, project_path: str, log_file: Optional[str] = None):
        self.project_path = project_path
        self._last_read_pos: int = 0

        # Log file for terminal A output
        if log_file:
            self.log_file = log_file
        else:
            # Create a default log file path
            self.log_file = os.path.join(
                tempfile.gettempdir(),
                "prdebug_terminal_a.log"
            )

    # ── Manual Mode Helpers ─────────────────────────────────────────────

    def format_command_for_user(self, command: str) -> str:
        """Format a command for the user to copy-paste into Terminal A."""
        # Wrap with logging redirect so output goes to our log file
        logged_cmd = f'({command}) 2>&1 | tee -a "{self.log_file}"'

        return (
            f"\n{'─' * 60}\n"
            f"  📋 Run this in Terminal A:\n"
            f"{'─' * 60}\n"
            f"\n  {command}\n"
            f"\n  (With logging): \n"
            f"  {logged_cmd}\n"
            f"{'─' * 60}\n"
        )

    def read_new_logs(self) -> str:
        """Read any new content from the Terminal A log file."""
        if not os.path.exists(self.log_file):
            return ""

        try:
            with open(self.log_file, "r", errors="replace") as f:
                f.seek(self._last_read_pos)
                new_content = f.read()
                self._last_read_pos = f.tell()
            return new_content
        except (PermissionError, OSError):
            return ""

    def read_all_logs(self) -> str:
        """Read entire Terminal A log file."""
        if not os.path.exists(self.log_file):
            return ""

        try:
            with open(self.log_file, "r", errors="replace") as f:
                content = f.read()
                self._last_read_pos = f.tell()
            return content
        except (PermissionError, OSError):
            return ""

    def reset_log_position(self):
        """Reset the log read position to the end (skip existing content)."""
        if os.path.exists(self.log_file):
            self._last_read_pos = os.path.getsize(self.log_file)

    def clear_log_file(self):
        """Clear the log file for a fresh start."""
        try:
            with open(self.log_file, "w") as f:
                f.write("")
            self._last_read_pos = 0
        except (PermissionError, OSError):
            pass

    # ── Auto Mode Execution ─────────────────────────────────────────────

    def execute_command(self, command: str, timeout: int = 600) -> tuple[int, str]:
        """
        Execute a command in the project directory and capture output.
        Returns (return_code, output).
        Used in auto mode.
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                errors="replace",
            )
            output = result.stdout
            if result.stderr:
                output += "\n--- STDERR ---\n" + result.stderr
            return result.returncode, output

        except subprocess.TimeoutExpired:
            return -1, f"[Command timed out after {timeout}s]: {command}"
        except Exception as e:
            return -1, f"[Command execution error]: {e}"

    def execute_command_streaming(self, command: str, timeout: int = 600) -> tuple[int, str]:
        """
        Execute a command with real-time output streaming to the log file.
        Used in auto mode for long-running commands like gradle builds.
        """
        output_lines = []
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=self.project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )

            # Also write to log file
            with open(self.log_file, "a") as log_f:
                log_f.write(f"\n{'='*60}\n")
                log_f.write(f"COMMAND: {command}\n")
                log_f.write(f"{'='*60}\n")

                for line in process.stdout:
                    output_lines.append(line)
                    log_f.write(line)
                    log_f.flush()

            process.wait(timeout=timeout)
            return process.returncode, "".join(output_lines)

        except subprocess.TimeoutExpired:
            process.kill()
            return -1, "".join(output_lines) + f"\n[Timed out after {timeout}s]"
        except Exception as e:
            return -1, f"[Execution error]: {e}"

    # ── File Operations (for auto mode) ─────────────────────────────────

    def read_project_file(self, relative_path: str) -> str:
        """Read a file from the project directory."""
        full_path = os.path.join(self.project_path, relative_path)
        try:
            with open(full_path, "r", errors="replace") as f:
                return f.read()
        except (FileNotFoundError, PermissionError, OSError) as e:
            return f"[Error reading {relative_path}: {e}]"

    def write_project_file(self, relative_path: str, content: str) -> bool:
        """Write content to a file in the project directory. Only for build files!"""
        full_path = os.path.join(self.project_path, relative_path)

        # Safety: only allow writing to build-related files
        allowed_extensions = (
            "build.gradle", "build.gradle.kts",
            "settings.gradle", "settings.gradle.kts",
            "gradle.properties", "gradle-wrapper.properties",
            "proguard-rules.pro", "proguard-rules.txt",
            "libs.versions.toml",
        )
        basename = os.path.basename(full_path)
        if not any(basename.endswith(ext) or basename == ext for ext in allowed_extensions):
            print(f"  ⛔ BLOCKED: Cannot write to non-build file: {relative_path}")
            return False

        try:
            # Backup original
            if os.path.exists(full_path):
                backup_path = full_path + ".prdebug_backup"
                if not os.path.exists(backup_path):  # don't overwrite first backup
                    with open(full_path, "r") as src, open(backup_path, "w") as dst:
                        dst.write(src.read())

            with open(full_path, "w") as f:
                f.write(content)
            return True
        except (PermissionError, OSError) as e:
            print(f"  ⛔ Error writing {relative_path}: {e}")
            return False

    def list_project_files(self, pattern: str = "**/*") -> list[str]:
        """List files in the project directory matching a pattern."""
        import glob as g
        full_pattern = os.path.join(self.project_path, pattern)
        return [
            os.path.relpath(f, self.project_path)
            for f in g.glob(full_pattern, recursive=True)
            if os.path.isfile(f)
        ]
