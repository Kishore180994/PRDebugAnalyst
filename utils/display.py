"""
Display utilities for the PR Debug Analyst CLI interface.
Built on the `rich` library for polished, Claude Code-style terminal UI.

Features:
  - Rich markdown rendering for agent messages
  - Animated spinners
  - Dashboard-style panels for commands, edits, verdicts
  - Auto-copy to clipboard for single commands
  - Copyable plain-text command blocks (no box-drawing interference)
  - Web event emission for live web dashboard
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
from rich.align import Align
from rich import box

import threading
import time

from utils.web_events import emitter, EventType

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
    "primary":     "bold cyan",
    "secondary":   "bold magenta",
    "accent":      "bold yellow",
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
    emitter.emit(EventType.SPINNER_START, message=msg)


def progress_done(label: str = "done"):
    """Stop the active spinner with a success checkmark."""
    global _active_spinner
    if _active_spinner:
        msg = _active_spinner.message
        _active_spinner.stop(f"  [green]✓[/green] [dim]{msg}[/dim] [dim]— {label}[/dim]")
        _active_spinner = None
        emitter.emit(EventType.SPINNER_DONE, message=msg, label=label)


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
#  Banner — Large, colorful ASCII art style
# ═══════════════════════════════════════════════════════════════════════════

def banner():
    """Print the application banner with stylized title."""
    console.print()

    # Stylized title art
    title_text = Text()
    title_text.append("╔══════════════════════════════════════════════════════╗\n", style="cyan")
    title_text.append("║  ", style="cyan")
    title_text.append("PR DEBUG ANALYST", style="bold magenta")
    title_text.append("  ", style="cyan")
    title_text.append("║\n", style="cyan")
    title_text.append("╚══════════════════════════════════════════════════════╝", style="cyan")

    console.print(Align.center(title_text))
    console.print()

    # Info row
    info_text = Text()
    info_text.append("v1.0  ", style="dim yellow")
    info_text.append("•  ", style="dim")
    info_text.append("AI-powered Android build failure debugger", style="dim cyan")
    info_text.append("  •  ", style="dim")
    info_text.append("Gemini", style="dim yellow")
    console.print(Align.center(info_text))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Section / Headers — Bold colored panel headers
# ═══════════════════════════════════════════════════════════════════════════

def section(title: str):
    """Print a section divider with bold colored header."""
    console.print()
    header_text = Text()
    header_text.append("  ▌ ", style="bold cyan")
    header_text.append(title, style="bold cyan")
    header_text.append("  ", style="bold cyan")
    console.print(Panel(
        header_text,
        border_style="cyan",
        padding=(0, 1),
        box=box.ROUNDED,
        expand=False,
    ))
    console.print()
    emitter.emit(EventType.SECTION, title=title)


def subsection(title: str):
    """Print a lighter subsection header."""
    console.print(f"\n  [cyan]▸ {title}[/cyan]")


# ═══════════════════════════════════════════════════════════════════════════
#  Status Messages
# ═══════════════════════════════════════════════════════════════════════════

def success(msg: str):
    console.print(f"  [green]✓[/green] {msg}")
    emitter.emit(EventType.SUCCESS, message=msg)

def error(msg: str):
    console.print(f"  [red]✗[/red] {msg}")
    emitter.emit(EventType.ERROR, message=msg)

def warning(msg: str):
    console.print(f"  [yellow]![/yellow] [yellow]{msg}[/yellow]")
    emitter.emit(EventType.WARNING, message=msg)

def info(msg: str):
    console.print(f"  [dim]│[/dim] {msg}")
    emitter.emit(EventType.INFO, message=msg)

def diminfo(msg: str):
    """Even more subtle info line."""
    console.print(f"  [dim]│ {msg}[/dim]")


# ═══════════════════════════════════════════════════════════════════════════
#  Agent Message — Rich Markdown rendering with colored left border
# ═══════════════════════════════════════════════════════════════════════════

def agent_msg(msg: str):
    """Render the AI agent's response using rich Markdown inside a dashboard panel."""
    console.print()
    md = Markdown(msg, code_theme="monokai")

    # Create a visually distinct agent panel with colored left border
    console.print(
        Panel(
            md,
            title="[bold magenta]🤖 Agent Response[/bold magenta]",
            title_align="left",
            border_style="magenta",
            padding=(1, 2),
            expand=True,
            box=box.ROUNDED,
        )
    )
    console.print()
    emitter.emit(EventType.AGENT_MESSAGE, content=msg)


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
#  Script Setup — Prominent Terminal A recording setup
# ═══════════════════════════════════════════════════════════════════════════

def script_setup_display(script_cmd: str, log_file: str):
    """
    Display a prominent setup panel telling the user to paste the script
    command into Terminal A. This is the FIRST thing the user should do
    before any agent interaction begins.
    """
    console.print()

    # Build the content
    content = Text()
    content.append("Paste this command in Terminal A to start recording:\n\n", style="dim")
    content.append(f"  {script_cmd}\n\n", style="bold white")
    content.append("This records all terminal output automatically.\n", style="dim")
    content.append(f"Log file: {log_file}\n", style="dim cyan")
    content.append("To stop recording later: type ", style="dim")
    content.append("exit", style="bold yellow")
    content.append(" or press ", style="dim")
    content.append("Ctrl+D", style="bold yellow")

    console.print(Panel(
        content,
        title="[bold yellow]⚡ TERMINAL A SETUP — DO THIS FIRST[/bold yellow]",
        title_align="center",
        border_style="yellow",
        padding=(1, 2),
        expand=True,
        box=box.DOUBLE,
    ))

    # Auto-copy to clipboard
    if _copy_to_clipboard(script_cmd):
        console.print(f"  [green]✓[/green] [dim]Command copied to clipboard — paste it in Terminal A[/dim]")
    else:
        console.print(f"  [dim]ℹ Copy the command above and paste it in Terminal A[/dim]")

    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Command Display — copyable, with auto-clipboard
# ═══════════════════════════════════════════════════════════════════════════

def command_display(command: str, auto_copy: bool = True):
    """
    Display a command for the user to run in Terminal A.
    Uses a dark-background panel with TERMINAL badge and syntax highlighting.
    """
    console.print()

    # Create syntax-highlighted command
    syntax = Syntax(command, "bash", theme="monokai", line_numbers=False, padding=1)

    # Terminal badge
    badge = Text()
    badge.append("⌘ TERMINAL", style="bold cyan on grey11")

    console.print(Panel(
        syntax,
        title=badge,
        title_align="left",
        border_style="cyan",
        padding=(0, 1),
        expand=True,
        box=box.ROUNDED,
    ))
    console.print()

    # Auto-copy to clipboard
    if auto_copy:
        if _copy_to_clipboard(command):
            console.print(f"  [green]✓[/green] [dim]Copied to clipboard[/dim]")
        else:
            console.print(f"  [dim]ℹ Tip: triple-click the command line to select & copy[/dim]")

    console.print(f"  [dim]Then press Enter here to scan the output[/dim]")
    console.print()
    emitter.emit(EventType.COMMAND, command=command, auto_copy=auto_copy)


def commands_display(commands: list[str]):
    """
    Display multiple commands in a numbered, syntax-highlighted panel.
    """
    console.print()

    # Build multi-line command display
    cmd_text = ""
    for i, cmd in enumerate(commands, 1):
        cmd_text += f"{i}. {cmd}\n"

    syntax = Syntax(cmd_text.rstrip(), "bash", theme="monokai", line_numbers=False, padding=1)

    console.print(Panel(
        syntax,
        title="[bold cyan]⌘ TERMINAL COMMANDS[/bold cyan]",
        title_align="left",
        border_style="cyan",
        padding=(0, 1),
        expand=True,
        box=box.ROUNDED,
    ))
    console.print()

    console.print(f"  [dim]ℹ Select a command line to copy it[/dim]")
    console.print(f"  [dim]Then press Enter here to scan the output[/dim]")
    console.print()
    emitter.emit(EventType.COMMANDS, commands=commands)


# ═══════════════════════════════════════════════════════════════════════════
#  Verdict Display — Full-width styled panel
# ═══════════════════════════════════════════════════════════════════════════

def verdict_display(verdict: str, reason: str):
    """Display the final verdict prominently with styled borders."""
    is_success = verdict == "BUILD_FIXED"
    style = "green" if is_success else "red"
    icon = "✅" if is_success else "❌"
    border_box = box.DOUBLE if is_success else box.HEAVY

    verdict_text = Text()
    verdict_text.append(f"{icon}  {verdict}\n\n", style=f"bold {style}")
    if reason:
        verdict_text.append(f"{reason}", style="dim")

    console.print()
    console.print(Panel(
        verdict_text,
        title=f"[bold {style}]VERDICT[/bold {style}]",
        title_align="center",
        border_style=style,
        padding=(1, 2),
        expand=True,
        box=border_box,
    ))
    console.print()
    emitter.emit(EventType.VERDICT, verdict=verdict, reason=reason)


# ═══════════════════════════════════════════════════════════════════════════
#  Log Summary Table — Rich table inside panel
# ═══════════════════════════════════════════════════════════════════════════

def log_summary_table(log_summaries: list[dict]):
    """Display historical log summaries in a rich formatted table."""
    if not log_summaries:
        warning("No log files found in the tasks folder.")
        return

    section(f"Historical Build Logs — {len(log_summaries)} file(s)")

    # Create table
    table = Table(
        show_header=True,
        box=box.ROUNDED,
        border_style="cyan",
        padding=(0, 1),
    )
    table.add_column("#", style="dim yellow", justify="center", width=3)
    table.add_column("PR ID", style="bold cyan", width=15)
    table.add_column("File", style="bold white", width=30)
    table.add_column("Errors", style="red", width=20)
    table.add_column("Failed Tasks", style="yellow", width=25)

    for i, log in enumerate(log_summaries, 1):
        pr_id = log.get("pr_id", "Unknown")
        filename = log.get("filename", "Unknown")
        errors = log.get("errors", [])
        failed_tasks = log.get("failed_tasks", [])

        # Format errors
        error_str = ""
        if errors:
            error_types = [f"{e['type']} ×{e['count']}" for e in errors[:2]]
            error_str = ", ".join(error_types)
        else:
            error_str = "[dim]none[/dim]"

        # Format failed tasks
        task_str = ""
        if failed_tasks:
            task_str = ", ".join(failed_tasks[:3])
        else:
            task_str = "[dim]none[/dim]"

        table.add_row(str(i), pr_id, filename, error_str, task_str)

    console.print(Panel(table, border_style="cyan", padding=(0, 0), box=box.ROUNDED))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Manual Mode Help — Table inside panel
# ═══════════════════════════════════════════════════════════════════════════

def manual_mode_help():
    """Display the manual mode action reference in a styled table."""
    console.print()
    table = Table(show_header=True, box=box.ROUNDED, border_style="cyan", padding=(0, 1))
    table.add_column("Key", style="bold yellow", justify="center", width=12)
    table.add_column("Action", style="white")

    table.add_row("[bold cyan]Enter[/bold cyan]", "Scan logs → denoise → feed to agent")
    table.add_row("[bold green]done[/bold green]", "Mark step as successful")
    table.add_row("[bold red]fail[/bold red]", "Mark step as failed")
    table.add_row("[bold yellow]quit[/bold yellow]", "Exit session")

    console.print(Panel(
        table,
        title="[bold cyan]⌨ Controls[/bold cyan]",
        border_style="cyan",
        padding=(0, 0),
        box=box.ROUNDED,
    ))
    console.print("  [dim]Or type any message to chat with the agent[/dim]")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Step indicator — Status bar style
# ═══════════════════════════════════════════════════════════════════════════

def step_prompt(step_num: int):
    """Display the step indicator before user input."""
    console.print()
    step_text = Text()
    step_text.append("━━━ ", style="dim cyan")
    step_text.append(f"Step {step_num}", style="bold cyan")
    step_text.append(" ━━━━━━━━━━━━━━━━━━━━━━━━━", style="dim cyan")
    console.print(step_text)
    console.print(f"  [dim]enter · done · fail · quit · or type[/dim]")
    emitter.emit(EventType.STEP, step_num=step_num)


# ═══════════════════════════════════════════════════════════════════════════
#  File edit preview — Syntax highlighted with header
# ═══════════════════════════════════════════════════════════════════════════

def file_edit_preview(filepath: str, content: str, max_lines: int = 15):
    """Show a preview of a file edit with syntax highlighting."""
    lines = content.split("\n")
    shown = "\n".join(lines[:max_lines])
    overflow = ""
    if len(lines) > max_lines:
        overflow = f"... +{len(lines) - max_lines} more lines"

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

    syntax = Syntax(shown, lang, theme="monokai", line_numbers=True, padding=1)

    # Build header with filename and line info
    header_text = Text()
    header_text.append("✏ ", style="bold yellow")
    header_text.append(filepath, style="bold white")
    header_text.append(f"  ({len(lines)} lines)", style="dim yellow")

    console.print()
    console.print(Panel(
        syntax,
        title=header_text,
        title_align="left",
        subtitle=overflow if overflow else None,
        border_style="yellow",
        padding=(0, 1),
        box=box.ROUNDED,
    ))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Thinking Block
# ═══════════════════════════════════════════════════════════════════════════

def thinking_start(topic: str = ""):
    """Show the start of a thinking/reasoning block."""
    label = f" {topic}" if topic else ""
    console.print(f"\n  [dim italic]▸ thinking{label}...[/dim italic]")
    emitter.emit(EventType.THINKING_START, topic=topic)


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
#  Tool Use Indicators — Colored inline badges
# ═══════════════════════════════════════════════════════════════════════════

def tool_use(tool_name: str, detail: str = ""):
    """Show a tool being invoked with colored badge."""
    badge = Text()
    badge.append(f"[{tool_name}]", style="bold cyan on grey11")
    detail_text = f"  [dim]{detail}[/dim]" if detail else ""
    console.print(f"  {badge}{detail_text}")
    emitter.emit(EventType.TOOL_USE, name=tool_name, detail=detail)


def tool_result(tool_name: str, status: str = "success", detail: str = ""):
    """Show the result of a tool call with status badge."""
    if status == "success":
        icon = "✓"
        style = "green"
    elif status == "error":
        icon = "✗"
        style = "red"
    else:
        icon = "!"
        style = "yellow"

    badge = Text()
    badge.append(f"[{tool_name}]", style=f"bold {style} on grey11")
    detail_text = f"  [dim]{detail}[/dim]" if detail else ""
    console.print(f"  [{style}]{icon}[/{style}] {badge}{detail_text}")
    emitter.emit(EventType.TOOL_RESULT, name=tool_name, status=status, detail=detail)


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
#  Session Stats — Horizontal layout with colored boxes
# ═══════════════════════════════════════════════════════════════════════════

def session_stats(steps: int, turns: int, duration_sec: float = 0):
    """Show session statistics in a compact dashboard format."""
    console.print()

    # Build columns
    stats_cols = []

    # Steps box
    steps_text = Text(str(steps), style="bold yellow")
    stats_cols.append(Panel(
        steps_text,
        title="[dim]Steps[/dim]",
        border_style="yellow",
        padding=(0, 2),
        expand=True,
        box=box.ROUNDED,
    ))

    # Turns box
    turns_text = Text(str(turns), style="bold cyan")
    stats_cols.append(Panel(
        turns_text,
        title="[dim]Turns[/dim]",
        border_style="cyan",
        padding=(0, 2),
        expand=True,
        box=box.ROUNDED,
    ))

    # Time box
    if duration_sec > 0:
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        time_str = f"{mins}m {secs}s"
        time_text = Text(time_str, style="bold magenta")
        stats_cols.append(Panel(
            time_text,
            title="[dim]Time[/dim]",
            border_style="magenta",
            padding=(0, 2),
            expand=True,
            box=box.ROUNDED,
        ))

    console.print(Columns(stats_cols, expand=True))
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
#  Final Report Display — Full dashboard panels with tables
# ═══════════════════════════════════════════════════════════════════════════

def report_success(pr_link: str, root_cause: str, fix: str, files: list[dict], steps: list):
    """Display a polished success report with dashboard panels."""
    console.print()

    # Main verdict
    verdict_panel = Text()
    verdict_panel.append("✅  BUILD FIXED\n\n", style="bold green")

    # Info table
    info_table = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
    info_table.add_column("Label", style="dim", width=15)
    info_table.add_column("Value", style="white")

    info_table.add_row("PR", pr_link)
    info_table.add_row("Root cause", root_cause)
    info_table.add_row("Fix", fix)

    console.print(Panel(
        info_table,
        title="[bold green]Success Report[/bold green]",
        border_style="green",
        padding=(1, 1),
        expand=True,
        box=box.ROUNDED,
    ))

    # Files changed
    if files:
        console.print()
        files_table = Table(show_header=True, box=box.ROUNDED, border_style="green", padding=(0, 1))
        files_table.add_column("File", style="bold white")
        files_table.add_column("Change", style="dim")

        for fc in files:
            files_table.add_row(fc['file'], fc['change'])

        console.print(Panel(
            files_table,
            title="[bold green]Files Changed[/bold green]",
            border_style="green",
            padding=(0, 0),
            box=box.ROUNDED,
        ))

    # Steps
    if steps:
        console.print()
        steps_table = Table(show_header=True, box=box.ROUNDED, border_style="green", padding=(0, 1))
        steps_table.add_column("#", justify="center", width=3)
        steps_table.add_column("Step", style="white")
        steps_table.add_column("Result", justify="center", width=10)

        for idx, step in enumerate(steps, 1):
            desc = step.description if hasattr(step, 'description') else str(step)
            result = step.result if hasattr(step, 'result') else ""

            if result == "success":
                result_str = "[green]✓[/green]"
            elif result == "failed":
                result_str = "[red]✗[/red]"
            else:
                result_str = "[dim]→[/dim]"

            steps_table.add_row(str(idx), desc, result_str)

        console.print(Panel(
            steps_table,
            title="[bold green]Steps[/bold green]",
            border_style="green",
            padding=(0, 0),
            box=box.ROUNDED,
        ))

    console.print()


def report_failure(pr_link: str, verdict: str, root_cause: str, why: str,
                   steps: list, hist_errors: list, live_errors: list):
    """Display a polished failure report with dashboard panels."""
    console.print()

    # Info table
    info_table = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
    info_table.add_column("Label", style="dim", width=15)
    info_table.add_column("Value", style="white")

    info_table.add_row("PR", pr_link)
    info_table.add_row("Verdict", verdict)
    info_table.add_row("Root cause", root_cause or "Could not determine")

    console.print(Panel(
        info_table,
        title=f"[bold red]Failure Report[/bold red]",
        border_style="red",
        padding=(1, 1),
        expand=True,
        box=box.ROUNDED,
    ))

    # Historical errors
    if hist_errors:
        console.print()
        hist_table = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
        hist_table.add_column("", width=1, style="red")
        hist_table.add_column("Error", style="dim")

        for err in hist_errors[:5]:
            hist_table.add_row("•", err)

        console.print(Panel(
            hist_table,
            title="[bold red]Historical Errors[/bold red]",
            border_style="red",
            padding=(0, 1),
            box=box.ROUNDED,
        ))

    # Live errors
    if live_errors:
        console.print()
        live_table = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
        live_table.add_column("", width=1, style="red")
        live_table.add_column("Error", style="red")

        for err in live_errors[:5]:
            live_table.add_row("•", err)

        console.print(Panel(
            live_table,
            title="[bold red]Live Errors[/bold red]",
            border_style="red",
            padding=(0, 1),
            box=box.ROUNDED,
        ))

    # Steps attempted
    if steps:
        console.print()
        steps_table = Table(show_header=True, box=box.ROUNDED, border_style="red", padding=(0, 1))
        steps_table.add_column("#", justify="center", width=3)
        steps_table.add_column("Step", style="white")
        steps_table.add_column("Result", justify="center", width=10)

        for idx, step in enumerate(steps, 1):
            desc = step.description if hasattr(step, 'description') else str(step)
            result = step.result if hasattr(step, 'result') else ""

            if result == "success":
                result_str = "[green]✓[/green]"
            elif result == "failed":
                result_str = "[red]✗[/red]"
            else:
                result_str = "[dim]→[/dim]"

            steps_table.add_row(str(idx), desc, result_str)

        console.print(Panel(
            steps_table,
            title="[bold red]Steps Attempted[/bold red]",
            border_style="red",
            padding=(0, 0),
            box=box.ROUNDED,
        ))

    # Why unfixable
    if why:
        console.print()
        console.print(Panel(
            Text(why, style="dim"),
            title="[bold red]Why Unfixable[/bold red]",
            border_style="red",
            padding=(1, 1),
            box=box.ROUNDED,
        ))

    console.print()


def script_generated(script_path: str, script_name: str):
    """Announce the generated fix script."""
    console.print()

    script_text = Text()
    script_text.append("⬡ Fix script generated\n\n", style="bold green")
    script_text.append(script_name, style="bold white")
    script_text.append("\n\n", style="dim")

    run_cmd = f"cd /path/to/project && bash {script_name}"
    script_text.append(run_cmd, style="dim")

    console.print(Panel(
        script_text,
        border_style="green",
        padding=(1, 1),
        box=box.ROUNDED,
    ))

    # Auto-copy the run command
    if _copy_to_clipboard(run_cmd):
        console.print(f"  [green]✓[/green] [dim]Run command copied to clipboard[/dim]")

    console.print()
