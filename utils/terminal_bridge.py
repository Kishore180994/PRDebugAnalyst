"""
Terminal Bridge - Handles communication between Terminal A (project) and Terminal B (agent).
Manages log file watching and command execution for both manual and auto modes.

Uses the `script` command for terminal session recording in manual mode,
which captures all terminal output (including interactive programs) without
requiring the user to append `tee` to every command.
"""
import os
import subprocess
import time
import tempfile
import platform
from pathlib import Path
from typing import Optional

# Max characters to keep per tool response in history (prevents context blowup).
# ~30k chars ≈ ~7.5k tokens — keeps responses manageable in the conversation.
MAX_TOOL_RESPONSE_CHARS = 30_000


class TerminalBridge:
    """
    Bridge between the agent (Terminal B) and the project terminal (Terminal A).

    In manual mode:
        - Uses `script` command to record Terminal A session to a log file
        - Reads log output from the script-generated typescript file

    In auto mode:
        - Executes commands directly via subprocess
        - Captures output in real-time
    """

    @staticmethod
    def _truncate_output(text: str, max_chars: int = MAX_TOOL_RESPONSE_CHARS) -> str:
        """
        Truncate tool output to prevent context window blowup.
        Keeps head + tail so the agent sees the beginning and end of output.
        """
        if len(text) <= max_chars:
            return text
        half = max_chars // 2
        return (
            text[:half]
            + f"\n\n... [TRUNCATED {len(text) - max_chars:,} chars] ...\n\n"
            + text[-half:]
        )

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

    def get_script_command(self) -> str:
        """
        Return the `script` command that the user should run in Terminal A
        to start recording their session. This captures ALL terminal output
        automatically — no need to append tee/redirect to each command.
        """
        system = platform.system()
        if system == "Darwin":
            # macOS: script -a -F <file>  (-F flushes after each write)
            return f'script -a -F "{self.log_file}"'
        elif system == "Linux":
            # Linux: script -a -f <file>  (-f flushes after each write)
            return f'script -a -f "{self.log_file}"'
        else:
            # Fallback for other systems
            return f'script -a "{self.log_file}"'

    def get_stop_script_hint(self) -> str:
        """How to stop the script recording session."""
        return "Type 'exit' or press Ctrl+D to stop recording."

    def format_command_for_user(self, command: str) -> str:
        """Format a command for the user to copy-paste into Terminal A."""
        return (
            f"\n{'─' * 60}\n"
            f"  Run this in Terminal A:\n"
            f"{'─' * 60}\n"
            f"\n  {command}\n"
            f"{'─' * 60}\n"
        )

    def read_new_logs(self) -> str:
        """
        Read any new content from the Terminal A log file.
        Strips ANSI escape sequences that `script` command captures.
        """
        if not os.path.exists(self.log_file):
            return ""

        try:
            with open(self.log_file, "r", errors="replace") as f:
                f.seek(self._last_read_pos)
                new_content = f.read()
                self._last_read_pos = f.tell()
            cleaned = self._strip_ansi(new_content)
            # Cap terminal log size to prevent context blowup
            return self._truncate_output(cleaned)
        except (PermissionError, OSError):
            return ""

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI escape sequences from text (produced by `script` command)."""
        import re
        # Strip ANSI escape sequences: CSI sequences, OSC sequences, and simple escapes
        ansi_pattern = re.compile(
            r'\x1b\[[0-9;]*[a-zA-Z]'   # CSI sequences (colors, cursor, etc.)
            r'|\x1b\][^\x07]*\x07'      # OSC sequences (title bar, etc.)
            r'|\x1b[()][A-Z0-9]'        # Character set selection
            r'|\x1b[>=<]'               # Keypad/cursor modes
            r'|\r'                       # Carriage returns (script often has \r\n)
        )
        return ansi_pattern.sub('', text)

    def read_all_logs(self) -> str:
        """Read entire Terminal A log file. Strips ANSI sequences from script output."""
        if not os.path.exists(self.log_file):
            return ""

        try:
            with open(self.log_file, "r", errors="replace") as f:
                content = f.read()
                self._last_read_pos = f.tell()
            return self._strip_ansi(content)
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
        Returns (return_code, truncated_output).
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
            return result.returncode, self._truncate_output(output)

        except subprocess.TimeoutExpired:
            return -1, f"[Command timed out after {timeout}s]: {command}"
        except Exception as e:
            return -1, f"[Command execution error]: {e}"

    def execute_command_streaming(self, command: str, timeout: int = 600) -> tuple[int, str]:
        """
        Execute a command with real-time output streaming to the log file.
        Used in auto mode for long-running commands like gradle builds.
        Properly kills the process on timeout and waits for cleanup.
        """
        output_lines = []
        process = None
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

                try:
                    for line in process.stdout:
                        output_lines.append(line)
                        log_f.write(line)
                        log_f.flush()

                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()  # Wait for kill to complete, prevent zombies
                    return -1, self._truncate_output("".join(output_lines) + f"\n[Timed out after {timeout}s]")

            return process.returncode, self._truncate_output("".join(output_lines))

        except Exception as e:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            return -1, f"[Execution error]: {e}"

    # ── File Operations (for auto mode) ─────────────────────────────────

    def _validate_project_path(self, relative_path: str) -> tuple[bool, str]:
        """
        Validate that a relative path stays within the project directory.
        Returns (is_valid, resolved_full_path).
        Prevents path traversal attacks (e.g., ../../etc/passwd).
        """
        full_path = os.path.join(self.project_path, relative_path)
        canonical_project = os.path.realpath(self.project_path)
        canonical_full = os.path.realpath(full_path)

        if not canonical_full.startswith(canonical_project + os.sep) and canonical_full != canonical_project:
            return False, full_path
        return True, full_path

    def read_project_file(self, path: str, start_line: int = 1, num_lines: int = 1000) -> str:
        """
        Read a file from the project directory with optional line range.
        Matches PRFAgent's read_project_file API with start_line/num_lines.

        Args:
            path: Relative path within the project
            start_line: 1-based line to start reading from (default: 1)
            num_lines: Number of lines to read (default: 1000)
        """
        is_valid, full_path = self._validate_project_path(path)
        if not is_valid:
            return f"Error: Access denied — path escapes project directory: {path}"
        try:
            if not os.path.exists(full_path):
                return "Error: File not found."
            with open(full_path, "r", errors="replace") as f:
                all_lines = f.readlines()
            total = len(all_lines)
            start_idx = max(0, start_line - 1)
            end_idx = start_idx + num_lines
            segment = all_lines[start_idx:end_idx]
            header = (
                f"--- FILE: {path} ---\n"
                f"--- SHOWING LINES {start_line} TO {min(end_idx, total)} OF {total} ---\n"
            )
            return self._truncate_output(header + "".join(segment))
        except (FileNotFoundError, PermissionError, OSError) as e:
            return f"Error: {e}"

    def list_directory(self, path: str = ".") -> str:
        """
        List files and directories inside the project root. Read-only.
        Returns formatted listing with [DIR] and [FILE] prefixes.
        """
        is_valid, target = self._validate_project_path(path)
        if not is_valid:
            return f"Error: Access denied — path escapes project directory: {path}"
        try:
            if not os.path.exists(target):
                return "Error: Path not found."
            items = sorted(
                os.listdir(target),
                key=lambda x: (not os.path.isdir(os.path.join(target, x)), x),
            )
            items = items[:100]  # Cap at 100 items
            result = "\n".join(
                f"[{'DIR ' if os.path.isdir(os.path.join(target, i)) else 'FILE'}] {i}"
                for i in items
            )
            return result
        except OSError as e:
            return f"Error: {e}"

    # Directories to always skip during grep/find — generated artifacts,
    # caches, and binaries that are huge and irrelevant to source analysis.
    EXCLUDE_DIRS = [
        "build", ".gradle", ".git", "node_modules", ".idea",
        "__pycache__", ".cxx", ".externalNativeBuild", ".kotlin",
        "captures", ".navigation", "intermediates", "generated",
        "tmp", "caches", "transforms", "wrapper",
    ]

    def grep_project(self, pattern: str, path: str = ".", file_glob: str = "",
                     max_results: int = 50) -> str:
        """
        Search for a pattern in project SOURCE files using grep.
        Automatically excludes build/, .gradle/, .git/, node_modules/, etc.
        to avoid scanning gigabytes of generated artifacts.

        Args:
            pattern: Regex pattern to search for
            path: Subdirectory to search in (relative to project root)
            file_glob: Optional glob to filter files (e.g., "*.gradle", "*.kt")
            max_results: Maximum number of matches to return
        """
        is_valid, target = self._validate_project_path(path)
        if not is_valid:
            return f"Error: Access denied — path escapes project directory: {path}"

        try:
            import subprocess as sp

            # -m limits grep to max matches PER FILE, preventing runaway output
            cmd = ["grep", "-rn", f"-m{max_results}"]

            # Exclude heavy directories (build artifacts, caches, .git objects)
            for d in self.EXCLUDE_DIRS:
                cmd.append(f"--exclude-dir={d}")

            # Exclude binary/archive files
            cmd.extend([
                "--exclude=*.jar", "--exclude=*.aar", "--exclude=*.class",
                "--exclude=*.dex", "--exclude=*.apk", "--exclude=*.so",
                "--exclude=*.png", "--exclude=*.jpg", "--exclude=*.webp",
                "--exclude=*.zip", "--exclude=*.tar.gz",
            ])

            if file_glob:
                cmd.append(f"--include={file_glob}")

            cmd.extend([pattern, target])

            result = sp.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                errors="replace",
            )

            lines = result.stdout.strip().split("\n")
            if not lines or (len(lines) == 1 and not lines[0]):
                return f"No matches found for pattern '{pattern}' in {path}"

            # Trim to max_results and make paths relative
            output_lines = []
            for line in lines[:max_results]:
                rel_line = line.replace(self.project_path + "/", "")
                output_lines.append(rel_line)

            result_text = "\n".join(output_lines)
            if len(lines) > max_results:
                result_text += f"\n\n... [{len(lines) - max_results} more matches truncated]"

            return self._truncate_output(f"--- GREP: '{pattern}' in {path} ({len(output_lines)} matches) ---\n{result_text}")

        except sp.TimeoutExpired:
            return f"Error: Search timed out for pattern '{pattern}'"
        except FileNotFoundError:
            return "Error: grep command not found on this system"
        except Exception as e:
            return f"Error: {e}"

    def find_files(self, name_pattern: str, path: str = ".", max_results: int = 30) -> str:
        """
        Find files by name pattern in the project.

        Args:
            name_pattern: Glob pattern for filename (e.g., "*.gradle.kts", "google-services*")
            path: Subdirectory to search in
            max_results: Maximum results to return
        """
        import glob as g

        is_valid, target = self._validate_project_path(path)
        if not is_valid:
            return f"Error: Access denied"

        full_pattern = os.path.join(target, "**", name_pattern)
        matches = g.glob(full_pattern, recursive=True)

        if not matches:
            return f"No files found matching '{name_pattern}' in {path}"

        # Make relative and cap
        results = [os.path.relpath(m, self.project_path) for m in matches[:max_results]]
        output = "\n".join(results)
        if len(matches) > max_results:
            output += f"\n\n... [{len(matches) - max_results} more files truncated]"

        return f"--- FIND: '{name_pattern}' in {path} ({len(results)} files) ---\n{output}"

    def run_setup_command(self, command: str, timeout: int = 120) -> str:
        """
        Execute a build environment setup command in the project directory.
        Used by the agent to install Java, SDK components, patch files, etc.

        SAFETY: Only allows commands that match an explicit allowlist of
        safe prefixes. Blocks anything potentially destructive.

        Returns the command output (stdout + stderr).
        """
        import subprocess as sp

        command = command.strip()

        # ── Safety: allowlist of safe command prefixes ──
        ALLOWED_PREFIXES = [
            # File patching (PRFAgent's core fixing strategy)
            "sed ", "cp ", "echo ", "cat ", "mkdir ", "touch ",
            # Downloads / fetching
            "wget ", "curl ",
            # Package managers (environment setup)
            "brew install", "brew tap",
            "sudo apt-get install", "sudo apt install", "apt-get install",
            "sudo dnf install", "dnf install",
            "sudo yum install", "yum install",
            # Java / Android SDK
            "sdkmanager ", "sdk install",
            "java -version", "javac -version",
            # Gradle
            "./gradlew ", "gradle ", "chmod +x gradlew",
            # Git
            "git ",
            # Environment
            "export ", "source ", "env ",
            # Shell utilities
            "which ", "ls ", "find ", "head ", "tail ", "wc ",
            "pwd", "printenv",
        ]

        # ── Safety: blocklist of dangerous patterns ──
        BLOCKED_PATTERNS = [
            "rm -rf /", "rm -rf ~", "rm -rf $HOME",
            "sudo rm ", "> /dev/", "mkfs", "dd if=",
            "chmod 777", ":(){", "fork bomb",
            "curl | sh", "curl | bash", "wget | sh",
            "python -c", "python3 -c",  # prevent arbitrary code execution
            "eval ", "exec ",
        ]

        # Check blocklist first
        cmd_lower = command.lower()
        for blocked in BLOCKED_PATTERNS:
            if blocked.lower() in cmd_lower:
                return f"BLOCKED: Command matches dangerous pattern '{blocked}'. Not executed."

        # Check allowlist
        allowed = False
        for prefix in ALLOWED_PREFIXES:
            if command.startswith(prefix) or command.startswith("sudo " + prefix):
                allowed = True
                break

        # Also allow piped commands where the first command is allowed
        if not allowed and "|" in command:
            first_cmd = command.split("|")[0].strip()
            for prefix in ALLOWED_PREFIXES:
                if first_cmd.startswith(prefix):
                    allowed = True
                    break

        # Also allow chained commands (&&) where all parts are allowed
        if not allowed and "&&" in command:
            parts = [p.strip() for p in command.split("&&")]
            if all(any(p.startswith(pfx) for pfx in ALLOWED_PREFIXES) for p in parts):
                allowed = True

        if not allowed:
            return (
                f"BLOCKED: Command '{command[:60]}...' is not in the allowed command list. "
                f"For safety, only build environment setup commands are allowed. "
                f"Ask the user to run this command manually in Terminal A."
            )

        # Execute the command
        try:
            result = sp.run(
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

            status = "SUCCESS" if result.returncode == 0 else f"FAILED (exit code {result.returncode})"
            return self._truncate_output(f"--- COMMAND: {command} ---\n--- STATUS: {status} ---\n{output}")

        except sp.TimeoutExpired:
            return f"TIMEOUT: Command timed out after {timeout}s: {command}"
        except Exception as e:
            return f"ERROR: {e}"

    def write_project_file(self, relative_path: str, content: str) -> bool:
        """Write content to a file in the project directory. Only for build files!"""
        # Validate path doesn't escape project directory
        is_valid, full_path = self._validate_project_path(relative_path)
        if not is_valid:
            print(f"  ⛔ BLOCKED: Path escapes project directory: {relative_path}")
            return False

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
