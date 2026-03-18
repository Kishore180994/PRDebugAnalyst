"""
Display utilities for the PR Debug Analyst CLI interface.
Built on the `rich` library for polished, Claude Code-style terminal UI.

Features:
  - Rich markdown rendering for agent messages
  - Animated spinners
  - Clean panels for commands, edits, verdicts
  - Auto-copy to clipboard for single commands
  - Copyable plain-text command blocks (no box-drawing interference)
"""
import os
import sys
import platform
import subprocess
import shutil
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.table import Table
from rich.columns import Columns
from rich.syntax import Syntax
from rich.spinner import Spinner as RichSpinner
from rich.live import Live
from rich.theme import Theme
from rich import box

import threading
import time

# ═══════════════════════════════════════════════════════════════════════════
#  Theme & Console
# ═══════════════════════════════════════════════════════════════════════════

_THEME = Theme({
    "info":        "dim",
    "info.label":  "dim cyan",
    "success":     "green",
    "warning":     "yellow",
    "error":       "red",
    "agent":       "medium_purple1",
    "agent.name":  "bold medium_purple1",
    "thinking":    "dim italic",
    "tool":        "cyan",
    "tool.name":   "bold cyan",
    "command":     "bold white on grey23",
    "command.label": "bold yellow",
    "file":        "bold white",
    "dim":         "dim",
    "verdict.pass": "bold green",
    "verdict.fail": "bold red",
    "step":        "bold blue",
    "section":     "bold blue",
})

console = Console(theme=_THEME, highlight=False)

# ═══════════════════════════════════════════════════════════════════════════
#  Clipboard Utility
# ═══════════════════════════════════════════════════════════════════════════

def _copy_to_clipboard(text: str) -> bool:
    """
    Copy text to system clipboard. Returns True on success.
    Uses native OS commands for reliability (no X server needed for macOS).
    """
    text = text.strip()

    # macOS
    if platform.system() == "Darwin":
        try:
            subprocess.run(["pbcopy"], input=text.encode(), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    # Linux — try xclip, then xsel, then wl-copy (Wayland)
    if platform.system() == "Linux":
        for cmd in [["xclip", "-selection", "clipboard"],
                    ["xsel", "--clipboard", "--input"],
                    ["wl-copy"]]:
            try:
                subprocess.run(cmd, input=text.encode(), check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue

    # Windows
    if platform.system() == "Windows":
        try:
            subprocess.run(["clip"], input=text.encode(), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    # Fallback: pyperclip
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    return False


# ═══════════════════════════════════════════════════════════════════════════
#  Animated Spinner (background thread, compatible with old API)
# ═══════════════════════════════════════════════════════════════════════════

class _Spinner:
    """Background-thread spinner using rich."""
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = ""):
        self.message = message
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, final_line: str = ""):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        # Clear spinner line
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()
        if final_line:
            console.print(final_line)

    def _spin(self):
        i = 0
        while self._running:
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r  [cyan]{frame}[/] [dim]{self.message}[/]"
                             .replace("[cyan]", "\033[36m")
                             .replace("[/]", "\033[0m")
                             .replace("[dim]", "\033[2m"))
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1


_active_spinner: Optional[_Spinner] = None


def progress_spinner(msg: str):
    """Start an animated spinner with a message."""
    global _active_spinner
    if _active_spinner:
        _active_spinner.stop()
    _active_spinner = _Spinner(msg)
    _active_spinner.start()


def progress_done(label: str = "done"):
    """Stop the active spinner with a success checkmark."""
    global _active_spinner
    if _active_spinner:
        _active_spinner.stop(f"  [green]✓[/green] [dim]{_active_spinner.message}[/dim] [dim]— {label}[/dim]")
        _active_spinner = None


def progress_cancel():
    """Stop the active spinner with a 'cancelled' label."""
    global _active_spinner
    if _active_spinner:
        _active_spinner.stop(f"  [yellow]⊘[/yellow] [dim]{_active_spinner.message}[/dim] [dim]— cancelled[/dim]")
        _active_spinner = None


def interrupted_msg():
    """Show a clean interrupted indicator."""
    console.print()
    console.print("  [yellow]⊘[/yellow] [yellow]Interrupted[/yellow] — returning to prompt")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Banner
# ═══════════════════════════════════════════════════════════════════════════

def banner():
    """Print the application banner."""
    console.print()
    console.print("  [bold blue]PR Debug Analyst[/bold blue]  [dim]v1.0[/dim]")
    console.print("  [dim]AI-powered Android build failure debugger • Gemini[/dim]")
    console.print(Rule(style="dim blue"))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Section / Headers
# ═══════════════════════════════════════════════════════════════════════════

def section(title: str):
    """Print a section divider."""
    console.print()
    console.print(Rule(f"[bold blue] {title} [/bold blue]", style="blue"))
    console.print()


def subsection(title: str):
    """Print a lighter subsection header."""
    console.print(f"\n  [cyan]{title}[/cyan]")


# ═══════════════════════════════════════════════════════════════════════════
#  Status Messages
# ═══════════════════════════════════════════════════════════════════════════

def success(msg: str):
    console.print(f"  [green]✓[/green] {msg}")

def error(msg: str):
    console.print(f"  [red]✗[/red] {msg}")

def warning(msg: str):
    console.print(f"  [yellow]![/yellow] [yellow]{msg}[/yellow]")

def info(msg: str):
    console.print(f"  [dim]│[/dim] {msg}")

def diminfo(msg: str):
    """Even more subtle info line."""
    console.print(f"  [dim]│ {msg}[/dim]")


# ═══════════════════════════════════════════════════════════════════════════
#  Agent Message — Rich Markdown rendering
# ═══════════════════════════════════════════════════════════════════════════

def agent_msg(msg: str):
    """Render the AI agent's response using rich Markdown inside a panel."""
    console.print()
    md = Markdown(msg, code_theme="monokai")
    console.print(
        Panel(
            md,
            title="[bold medium_purple1]Agent[/bold medium_purple1]",
            title_align="left",
            border_style="medium_purple1",
            padding=(1, 2),
            expand=True,
        )
    )
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  User Input
# ═══════════════════════════════════════════════════════════════════════════

def user_prompt(prompt_text: str = "") -> str:
    """Show a styled user input prompt."""
    if prompt_text:
        console.print(f"  [dim]{prompt_text}[/dim]")
    try:
        return input("  \033[1;34m❯\033[0m ")
    except EOFError:
        return "quit"


# ═══════════════════════════════════════════════════════════════════════════
#  Command Display — copyable, with auto-clipboard
# ═══════════════════════════════════════════════════════════════════════════

def command_display(command: str, auto_copy: bool = True):
    """
    Display a command for the user to run in Terminal A.

    The command is printed as plain text (no box-drawing around the actual
    command string) so terminal copy-paste grabs just the command.

    If auto_copy=True and this is a single command, it is automatically
    copied to the clipboard.
    """
    console.print()
    console.print("  [bold yellow]Run in Terminal A:[/bold yellow]")
    console.print()

    # Print the raw command as plain text — easy to triple-click & copy
    console.print(f"  {command}")
    console.print()

    # Auto-copy to clipboard
    if auto_copy:
        if _copy_to_clipboard(command):
            console.print(f"  [green]✓[/green] [dim]Copied to clipboard[/dim]")
        else:
            console.print(f"  [dim]ℹ Tip: triple-click the command line to select & copy[/dim]")

    console.print(f"  [dim]Then press Enter here to scan the output[/dim]")
    console.print()


def commands_display(commands: list[str]):
    """
    Display multiple commands. Does NOT auto-copy (user picks which to copy).
    Each command is on its own line for easy individual selection.
    """
    console.print()
    console.print("  [bold yellow]Run in Terminal A:[/bold yellow]")
    console.print()

    for i, cmd in enumerate(commands, 1):
        console.print(f"  [dim]{i}.[/dim] {cmd}")

    console.print()
    console.print(f"  [dim]ℹ Select a command line to copy it[/dim]")
    console.print(f"  [dim]Then press Enter here to scan the output[/dim]")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Verdict Display
# ═══════════════════════════════════════════════════════════════════════════

def verdict_display(verdict: str, reason: str):
    """Display the final verdict prominently."""
    is_success = verdict == "BUILD_FIXED"
    style = "green" if is_success else "red"
    icon = "✅" if is_success else "❌"

    verdict_text = Text()
    verdict_text.append(f"  {icon}  {verdict}\n\n", style=f"bold {style}")
    if reason:
        verdict_text.append(f"  {reason}", style="dim")

    console.print()
    console.print(Panel(
        verdict_text,
        border_style=style,
        padding=(1, 2),
        expand=True,
    ))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Log Summary Table
# ═══════════════════════════════════════════════════════════════════════════

def log_summary_table(log_summaries: list[dict]):
    """Display historical log summaries in a rich table."""
    if not log_summaries:
        warning("No log files found in the tasks folder.")
        return

    section(f"Historical Build Logs — {len(log_summaries)} file(s)")

    for i, log in enumerate(log_summaries, 1):
        pr_id = log.get("pr_id", "Unknown")
        filename = log.get("filename", "Unknown")
        errors = log.get("errors", [])
        failed_tasks = log.get("failed_tasks", [])
        rel_path = log.get("rel_path", filename)
        match_source = log.get("match_source", "")
        pr_refs = log.get("pr_refs_in_file", [])

        # Header
        badge = f"  [on grey23][green]{match_source}[/green][/on grey23]" if match_source else ""
        console.print(f"  [bold blue]{i}.[/bold blue] [bold white]{pr_id}[/bold white]  [dim]{rel_path}[/dim]{badge}")

        # PR refs
        if pr_refs:
            refs_str = ", ".join(pr_refs[:3])
            console.print(f"     [dim]refs: {refs_str}[/dim]")

        # Errors
        if errors:
            for err in errors[:3]:
                console.print(f"     [red]•[/red] {err['type']}  [dim]×{err['count']}[/dim]")
        else:
            console.print(f"     [dim]no recognized error patterns[/dim]")

        # Failed tasks
        if failed_tasks:
            tasks_str = ", ".join(failed_tasks[:5])
            console.print(f"     [yellow]failed:[/yellow] {tasks_str}")

        console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Manual Mode Help
# ═══════════════════════════════════════════════════════════════════════════

def manual_mode_help():
    """Display the manual mode action reference."""
    console.print()
    table = Table(show_header=False, box=None, padding=(0, 2), show_edge=False)
    table.add_column("Key", style="bold white on grey23", min_width=8, justify="center")
    table.add_column("Action")

    table.add_row("Enter",  "Scan logs → denoise → feed to agent")
    table.add_row("done",   "[green]Mark step as successful[/green]")
    table.add_row("fail",   "[red]Mark step as failed[/red]")
    table.add_row("quit",   "[yellow]Exit session[/yellow]")

    console.print(table)
    console.print("  [dim]Or type any message to chat with the agent[/dim]")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Step indicator
# ═══════════════════════════════════════════════════════════════════════════

def step_prompt(step_num: int):
    """Display the step indicator before user input."""
    console.print()
    console.print(Rule(style="dim"))
    console.print(f"  [bold blue]Step {step_num}[/bold blue]  [dim]enter · done · fail · quit · or type[/dim]")


# ═══════════════════════════════════════════════════════════════════════════
#  File edit preview
# ═══════════════════════════════════════════════════════════════════════════

def file_edit_preview(filepath: str, content: str, max_lines: int = 15):
    """Show a preview of a file edit."""
    lines = content.split("\n")
    shown = "\n".join(lines[:max_lines])
    overflow = ""
    if len(lines) > max_lines:
        overflow = f"\n[dim]... +{len(lines) - max_lines} more lines[/dim]"

    # Try to guess language from filepath
    lang = "groovy"
    if filepath.endswith(".kts"):
        lang = "kotlin"
    elif filepath.endswith(".properties"):
        lang = "properties"
    elif filepath.endswith(".toml"):
        lang = "toml"
    elif filepath.endswith(".xml"):
        lang = "xml"
    elif filepath.endswith(".pro") or filepath.endswith(".cfg"):
        lang = "text"

    syntax = Syntax(shown, lang, theme="monokai", line_numbers=False, padding=1)

    console.print()
    console.print(Panel(
        syntax,
        title=f"[bold orange1]Edit: {filepath}[/bold orange1]",
        title_align="left",
        subtitle=overflow if overflow else None,
        border_style="orange1",
        padding=(0, 1),
    ))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Thinking Block
# ═══════════════════════════════════════════════════════════════════════════

def thinking_start(topic: str = ""):
    """Show the start of a thinking/reasoning block."""
    label = f" {topic}" if topic else ""
    console.print(f"\n  [dim italic]▸ thinking{label}...[/dim italic]")


def thinking_end():
    """Close a thinking block."""
    pass


def thinking_summary(text: str, max_lines: int = 3):
    """Show a collapsed thinking summary."""
    lines = text.strip().split("\n")[:max_lines]
    console.print("  [dim]▸ reasoning[/dim]")
    for line in lines:
        display_line = line[:80] + "..." if len(line) > 80 else line
        console.print(f"    [dim]{display_line}[/dim]")
    if len(text.strip().split("\n")) > max_lines:
        console.print("    [dim]...[/dim]")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Tool Use Indicators
# ═══════════════════════════════════════════════════════════════════════════

def tool_use(tool_name: str, detail: str = ""):
    """Show a tool being invoked."""
    detail_text = f"  [dim]{detail}[/dim]" if detail else ""
    console.print(f"  [cyan]⬡[/cyan] [bold cyan]{tool_name}[/bold cyan]{detail_text}")


def tool_result(tool_name: str, status: str = "success", detail: str = ""):
    """Show the result of a tool call."""
    if status == "success":
        icon = "[green]✓[/green]"
    elif status == "error":
        icon = "[red]✗[/red]"
    else:
        icon = "[yellow]![/yellow]"
    detail_text = f"  [dim]{detail}[/dim]" if detail else ""
    console.print(f"  {icon} [dim]{tool_name}[/dim]{detail_text}")


# ═══════════════════════════════════════════════════════════════════════════
#  Continuous Work Flow
# ═══════════════════════════════════════════════════════════════════════════

def work_start(label: str = ""):
    """Visual separator indicating the agent is starting a work block."""
    suffix = f"  [dim]{label}[/dim]" if label else ""
    console.print(f"\n  [dim]┌{'─' * 50}[/dim]{suffix}")


def work_step(description: str):
    """A single step within a work block."""
    console.print(f"  [dim]│[/dim] {description}")


def work_end():
    """Close a work block."""
    console.print(f"  [dim]└{'─' * 50}[/dim]\n")


# ═══════════════════════════════════════════════════════════════════════════
#  Session Stats
# ═══════════════════════════════════════════════════════════════════════════

def session_stats(steps: int, turns: int, duration_sec: float = 0):
    """Show session statistics."""
    parts = [f"steps: {steps}", f"turns: {turns}"]
    if duration_sec > 0:
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        parts.append(f"time: {mins}m {secs}s")
    console.print(f"  [dim]{' · '.join(parts)}[/dim]")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Compact Log Lines
# ═══════════════════════════════════════════════════════════════════════════

def log_lines(raw_lines: str, max_lines: int = 8, label: str = ""):
    """Show a compact preview of log output."""
    lines = raw_lines.strip().split("\n")
    header = f"  [dim]output" + (f" ({label})" if label else "") + ":[/dim]"
    console.print(header)
    for line in lines[:max_lines]:
        display = line[:90] + "..." if len(line) > 90 else line
        console.print(f"  [dim]  {display}[/dim]")
    if len(lines) > max_lines:
        console.print(f"  [dim]  ... +{len(lines) - max_lines} more lines[/dim]")


# ═══════════════════════════════════════════════════════════════════════════
#  Final Report Display
# ═══════════════════════════════════════════════════════════════════════════

def report_success(pr_link: str, root_cause: str, fix: str, files: list[dict], steps: list):
    """Display a polished success report."""
    content = Text()
    content.append("  ✅  BUILD FIXED\n\n", style="bold green")
    content.append(f"  PR          ", style="dim")
    content.append(f"{pr_link}\n")
    content.append(f"  Root cause  ", style="dim")
    content.append(f"{root_cause}\n")
    content.append(f"  Fix         ", style="dim")
    content.append(f"{fix}\n")

    if files:
        content.append(f"\n  Files changed:\n", style="dim")
        for fc in files:
            content.append(f"    • ", style="green")
            content.append(f"{fc['file']}\n", style="bold white")
            content.append(f"      {fc['change']}\n", style="dim")

    if steps:
        content.append(f"\n  Steps:\n", style="dim")
        for step in steps:
            desc = step.description if hasattr(step, 'description') else str(step)
            result = step.result if hasattr(step, 'result') else ""
            if result == "success":
                content.append(f"    ✓ ", style="green")
            elif result == "failed":
                content.append(f"    ✗ ", style="red")
            else:
                content.append(f"    → ", style="dim")
            content.append(f"{desc}\n")

    console.print()
    console.print(Panel(content, border_style="green", padding=(1, 1), expand=True))


def report_failure(pr_link: str, verdict: str, root_cause: str, why: str,
                   steps: list, hist_errors: list, live_errors: list):
    """Display a polished failure report."""
    content = Text()
    content.append(f"  ❌  {verdict}\n\n", style="bold red")
    content.append(f"  PR          ", style="dim")
    content.append(f"{pr_link}\n")
    content.append(f"  Root cause  ", style="dim")
    content.append(f"{root_cause or 'Could not determine'}\n")

    if hist_errors:
        content.append(f"\n  Historical errors:\n", style="dim")
        for e in hist_errors[:5]:
            content.append(f"    • {e}\n", style="dim")

    if live_errors:
        content.append(f"\n  Live errors:\n", style="dim")
        for e in live_errors[:5]:
            content.append(f"    • ", style="red")
            content.append(f"{e}\n")

    if steps:
        content.append(f"\n  Steps attempted:\n", style="dim")
        for step in steps:
            desc = step.description if hasattr(step, 'description') else str(step)
            result = step.result if hasattr(step, 'result') else ""
            cmd = step.command if hasattr(step, 'command') and step.command else ""
            if result == "success":
                content.append(f"    ✓ ", style="green")
            elif result == "failed":
                content.append(f"    ✗ ", style="red")
            else:
                content.append(f"    → ", style="dim")
            content.append(f"{desc}\n")
            if cmd:
                content.append(f"      $ {cmd[:70]}\n", style="dim")

    if why:
        content.append(f"\n  Why unfixable:\n", style="dim")
        content.append(f"    {why}\n")

    console.print()
    console.print(Panel(content, border_style="red", padding=(1, 1), expand=True))


def script_generated(script_path: str, script_name: str):
    """Announce the generated fix script."""
    console.print()
    console.print(f"  [green]⬡[/green] [bold green]Fix script generated[/bold green]")
    console.print(f"    [bold white]{script_name}[/bold white]")

    run_cmd = f"cd /path/to/project && bash {script_name}"
    console.print(f"    [dim]{run_cmd}[/dim]")

    # Auto-copy the run command
    if _copy_to_clipboard(run_cmd):
        console.print(f"    [green]✓[/green] [dim]Run command copied to clipboard[/dim]")

    console.print()
