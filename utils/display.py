"""
Display utilities for the PR Debug Analyst CLI interface.
Built on the `rich` library — matches the PRFAgent display style.

Style guide (from PRFAgent.py):
  - Panel(Markdown(text)) for agent responses with code blocks extracted below
  - ⚡ TOOL: name for tool calls (yellow, inline)
  - Panel("...", border_style=color) for sections/phases
  - $ command in bold green for copyable commands
  - console.status() for AI thinking spinner
  - Web event emission for live web dashboard
"""
import os
import re
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
from rich.prompt import Prompt
from rich import box

from utils.web_events import emitter, EventType


# ═══════════════════════════════════════════════════════════════════════════
#  Console (record=True to allow saving session logs, like PRFAgent)
# ═══════════════════════════════════════════════════════════════════════════

console = Console(record=True, highlight=False)


# ═══════════════════════════════════════════════════════════════════════════
#  Clipboard Utility
# ═══════════════════════════════════════════════════════════════════════════

def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    text = text.strip()

    if platform.system() == "Darwin":
        try:
            subprocess.run(["pbcopy"], input=text.encode(), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

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

    if platform.system() == "Windows":
        try:
            subprocess.run(["clip"], input=text.encode(), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    return False


# ═══════════════════════════════════════════════════════════════════════════
#  Banner — Simple panel, like PRFAgent's _validate_and_setup header
# ═══════════════════════════════════════════════════════════════════════════

def banner():
    """Print the application banner."""
    console.print(Panel(
        "[bold cyan]PR DEBUG ANALYST[/bold cyan]",
        border_style="cyan",
    ))
    console.print(
        f"[bold]Platform:[/bold] {platform.system()} ({platform.machine()})  |  "
        f"[bold]Shell:[/bold] {os.environ.get('SHELL', 'unknown')}"
    )
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Section / Phase Headers — Full panel with colored text inside
# ═══════════════════════════════════════════════════════════════════════════

def section(title: str):
    """Print a section/phase header as a full-width colored panel."""
    console.print(Panel(
        f"[bold cyan]{title}[/bold cyan]",
        border_style="cyan",
    ))
    emitter.emit(EventType.SECTION, title=title)


def subsection(title: str):
    """Print a lighter subsection header."""
    console.print(f"\n[bold]{title}[/bold]")


# ═══════════════════════════════════════════════════════════════════════════
#  Status Messages
# ═══════════════════════════════════════════════════════════════════════════

def success(msg: str):
    console.print(f"  [green]✔ {msg}[/green]")
    emitter.emit(EventType.SUCCESS, message=msg)

def error(msg: str):
    console.print(f"  [bold red]✘ {msg}[/bold red]")
    emitter.emit(EventType.ERROR, message=msg)

def warning(msg: str):
    console.print(f"  [yellow]⚠ {msg}[/yellow]")
    emitter.emit(EventType.WARNING, message=msg)

def info(msg: str):
    console.print(f"  [dim]{msg}[/dim]")
    emitter.emit(EventType.INFO, message=msg)

def diminfo(msg: str):
    """Even more subtle info line."""
    console.print(f"  [dim]{msg}[/dim]")


# ═══════════════════════════════════════════════════════════════════════════
#  Agent Message — Panel(Markdown) + copyable code blocks below
#  (Exactly like PRFAgent's _print_ai_response)
# ═══════════════════════════════════════════════════════════════════════════

def agent_msg(msg: str, title: str = "[bold blue]🔧 AI Guide[/bold blue]",
              border_style: str = "blue"):
    """
    Render the AI agent's response using rich Markdown inside a panel.
    Extracts code blocks and prints them as copyable plain-text commands below.
    """
    if not msg or not msg.strip():
        return

    console.print(Panel(Markdown(msg), title=title, border_style=border_style))

    # Extract code blocks and print as copyable commands below the panel
    fence = "`" * 3
    code_blocks = re.findall(rf"{fence}(?:\w*)\n(.*?){fence}", msg, re.DOTALL)
    if code_blocks:
        console.print()
        console.print("[bold yellow]📋 Copyable commands (select below):[/bold yellow]")
        console.print("[dim]─" * 60 + "[/dim]")
        for i, block in enumerate(code_blocks, 1):
            if len(code_blocks) > 1:
                console.print(f"[dim]── block {i} ──[/dim]")
            console.print(block.strip())
            console.print("[dim]─" * 60 + "[/dim]")

    emitter.emit(EventType.AGENT_MESSAGE, content=msg)


# ═══════════════════════════════════════════════════════════════════════════
#  User Input — Using rich Prompt.ask like PRFAgent
# ═══════════════════════════════════════════════════════════════════════════

def user_prompt(prompt_text: str = "") -> str:
    """Show a styled user input prompt."""
    if prompt_text:
        return Prompt.ask(prompt_text, console=console)
    try:
        return input("  \033[1;34m❯\033[0m ")
    except EOFError:
        return "quit"


# ═══════════════════════════════════════════════════════════════════════════
#  Script Setup — Terminal A recording setup panel
# ═══════════════════════════════════════════════════════════════════════════

def script_setup_display(script_cmd: str, log_file: str):
    """Display setup panel for Terminal A script recording."""
    console.print(Panel(
        f"[bold yellow]RECORD SESSION[/bold yellow]\n\n"
        f"In [bold]Terminal A[/bold], run the command below to start recording logs.\n\n"
        f"[dim]Log file: {log_file}[/dim]",
        title="[bold red]TERMINAL A SETUP[/bold red]",
        border_style="red",
    ))
    print_copyable_commands([script_cmd])

    if _copy_to_clipboard(script_cmd):
        console.print(f"  [green]✔[/green] [dim]Copied to clipboard[/dim]")

    console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Command Display — Plain green $ lines (PRFAgent style)
# ═══════════════════════════════════════════════════════════════════════════

def print_copyable_commands(commands: list[str]):
    """Print commands as plain green $ lines — easy to select and copy."""
    console.print()
    for cmd in commands:
        console.print(f"  [bold green]$ {cmd}[/bold green]")
    console.print()


def command_display(command: str, auto_copy: bool = True):
    """Display a single command for the user to run in Terminal A."""
    print_copyable_commands([command])

    if auto_copy:
        if _copy_to_clipboard(command):
            console.print(f"  [green]✔[/green] [dim]Copied to clipboard[/dim]")

    console.print(f"  [dim]Then press Enter here to scan the output[/dim]")
    console.print()
    emitter.emit(EventType.COMMAND, command=command, auto_copy=auto_copy)


def commands_display(commands: list[str]):
    """Display multiple commands for the user."""
    print_copyable_commands(commands)
    console.print(f"  [dim]Then press Enter here to scan the output[/dim]")
    console.print()
    emitter.emit(EventType.COMMANDS, commands=commands)


# ═══════════════════════════════════════════════════════════════════════════
#  Verdict Display
# ═══════════════════════════════════════════════════════════════════════════

def verdict_display(verdict: str, reason: str):
    """Display the final verdict prominently."""
    is_success = verdict == "BUILD_FIXED"
    style = "green" if is_success else "red"
    icon = "✅" if is_success else "❌"

    content = f"[bold {style}]{icon}  {verdict}[/bold {style}]"
    if reason:
        content += f"\n\n{reason}"

    console.print(Panel(content, border_style=style))
    emitter.emit(EventType.VERDICT, verdict=verdict, reason=reason)


# ═══════════════════════════════════════════════════════════════════════════
#  Log Summary Table
# ═══════════════════════════════════════════════════════════════════════════

def log_summary_table(log_summaries: list[dict]):
    """Display historical log summaries."""
    if not log_summaries:
        warning("No log files found in the tasks folder.")
        return

    section(f"Historical Build Logs — {len(log_summaries)} file(s)")

    for i, log in enumerate(log_summaries, 1):
        pr_id = log.get("pr_id", "Unknown")
        filename = log.get("filename", "Unknown")
        errors = log.get("errors", [])
        failed_tasks = log.get("failed_tasks", [])

        console.print(f"  [bold]{i}. {pr_id}[/bold]  [dim]{filename}[/dim]")

        if errors:
            for err in errors[:3]:
                console.print(f"     [red]• {err['type']} ×{err['count']}[/red]")
        if failed_tasks:
            tasks_str = ", ".join(failed_tasks[:5])
            console.print(f"     [yellow]failed: {tasks_str}[/yellow]")
        console.print()


# ═══════════════════════════════════════════════════════════════════════════
#  Manual Mode Help
# ═══════════════════════════════════════════════════════════════════════════

def manual_mode_help():
    """Display the manual mode action reference."""
    console.print(Panel(
        "[bold green]Interactive Build Debugging[/bold green]\n"
        "Execute the recommended command in Terminal A, then press Enter.\n"
        "Type 'done' when build succeeds, 'fail' if giving up, or paste info.",
        border_style="green",
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  Step / Action Prompt — Like PRFAgent's Phase 2 prompt
# ═══════════════════════════════════════════════════════════════════════════

def step_prompt(step_num: int):
    """Display the step indicator before user input."""
    console.print()
    emitter.emit(EventType.STEP, step_num=step_num)


def action_prompt() -> str:
    """The main Phase 2 action prompt — matches PRFAgent's style."""
    return Prompt.ask(
        "\n[bold green]▶ Action[/bold green] (Enter=check logs, 'done'=success, 'fail'=give up)",
        console=console,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  File edit preview
# ═══════════════════════════════════════════════════════════════════════════

def file_edit_preview(filepath: str, content: str, max_lines: int = 15):
    """Show a preview of a file edit."""
    lines = content.split("\n")
    shown = "\n".join(lines[:max_lines])
    overflow = f"\n[dim]... +{len(lines) - max_lines} more lines[/dim]" if len(lines) > max_lines else ""

    lang = "groovy"
    if filepath.endswith(".kts"): lang = "kotlin"
    elif filepath.endswith(".properties"): lang = "properties"
    elif filepath.endswith(".toml"): lang = "toml"
    elif filepath.endswith(".xml"): lang = "xml"
    elif filepath.endswith((".pro", ".cfg")): lang = "text"

    syntax = Syntax(shown, lang, theme="monokai", line_numbers=True, padding=1)
    console.print(Panel(
        syntax,
        title=f"[bold yellow]✏ {filepath}[/bold yellow]",
        border_style="yellow",
        subtitle=overflow if overflow else None,
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  Thinking / Spinner — Use console.status() like PRFAgent
# ═══════════════════════════════════════════════════════════════════════════

# For compatibility with callers that use progress_spinner/progress_done
_spinner_context = None

def progress_spinner(msg: str):
    """Start a spinner. Compatible API but uses console.status internally."""
    global _spinner_context
    # We can't use context manager style, so just print a status line
    console.print(f"  [dim]⏳ {msg}…[/dim]")
    emitter.emit(EventType.SPINNER_START, message=msg)

def progress_done(label: str = "done"):
    """Stop spinner with success."""
    console.print(f"  [green]✔[/green] [dim]{label}[/dim]")
    emitter.emit(EventType.SPINNER_DONE, message="", label=label)

def progress_cancel():
    """Stop spinner with cancel."""
    console.print(f"  [yellow]⊘[/yellow] [dim]cancelled[/dim]")

def interrupted_msg():
    """Show interrupted indicator."""
    console.print("\n[yellow]Interrupted. Type 'fail' to conclude or press Enter to continue.[/yellow]")

def thinking_start(topic: str = ""):
    """Show thinking indicator."""
    label = f" {topic}" if topic else ""
    console.print(f"  [dim]🧠 thinking{label}…[/dim]")
    emitter.emit(EventType.THINKING_START, topic=topic)

def thinking_end():
    pass

def thinking_summary(text: str, max_lines: int = 3):
    """Show a collapsed thinking summary."""
    lines = text.strip().split("\n")[:max_lines]
    for line in lines:
        console.print(f"  [dim]{line[:80]}[/dim]")


# ═══════════════════════════════════════════════════════════════════════════
#  Tool Use Indicators — PRFAgent style: ⚡ TOOL: name (N/M)
# ═══════════════════════════════════════════════════════════════════════════

def tool_use(tool_name: str, detail: str = "", count: int = 0, budget: int = 0):
    """Show a tool being invoked — PRFAgent style."""
    budget_str = f" [dim]({count}/{budget})[/dim]" if budget > 0 else ""
    detail_str = f" [dim]{detail}[/dim]" if detail else ""
    console.print(f"[bold yellow]⚡ TOOL: {tool_name}[/bold yellow]{budget_str}{detail_str}")
    emitter.emit(EventType.TOOL_USE, name=tool_name, detail=detail)


def tool_result(tool_name: str, status: str = "success", detail: str = ""):
    """Show the result of a tool call."""
    if status == "success":
        console.print(f"  [dim]└─ ✔ {tool_name}: {detail}[/dim]")
    elif status == "error":
        console.print(f"  [dim red]└─ ✘ {tool_name}: {detail}[/dim red]")
    else:
        console.print(f"  [dim]└─ {tool_name}: {detail}[/dim]")
    emitter.emit(EventType.TOOL_RESULT, name=tool_name, status=status, detail=detail)


# ═══════════════════════════════════════════════════════════════════════════
#  Work Flow (kept for auto mode compatibility)
# ═══════════════════════════════════════════════════════════════════════════

def work_start(label: str = ""):
    pass

def work_step(description: str):
    console.print(f"  [dim]│ {description}[/dim]")

def work_end():
    pass


# ═══════════════════════════════════════════════════════════════════════════
#  Session Stats
# ═══════════════════════════════════════════════════════════════════════════

def session_stats(steps: int, turns: int, duration_sec: float = 0):
    """Show session statistics."""
    parts = [f"Steps: {steps}", f"Turns: {turns}"]
    if duration_sec > 0:
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        parts.append(f"Time: {mins}m {secs}s")
    console.print(f"  [dim]{' │ '.join(parts)}[/dim]")
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
#  Final Report Display — Panel(text) style like PRFAgent
# ═══════════════════════════════════════════════════════════════════════════

def report_success(pr_link: str, root_cause: str, fix: str, files: list[dict], steps: list):
    """Display a success report."""
    lines = [
        f"[bold green]✅ BUILD FIXED[/bold green]\n",
        f"  PR:         {pr_link}",
        f"  Root cause: {root_cause}",
        f"  Fix:        {fix}",
    ]

    if files:
        lines.append("\n  [bold]Files changed:[/bold]")
        for fc in files:
            lines.append(f"    • {fc['file']}: {fc['change']}")

    if steps:
        lines.append("\n  [bold]Steps:[/bold]")
        for step in steps:
            desc = step.description if hasattr(step, 'description') else str(step)
            result = step.result if hasattr(step, 'result') else ""
            icon = "✔" if result == "success" else "✘" if result == "failed" else "→"
            lines.append(f"    {icon} {desc}")

    console.print(Panel("\n".join(lines), border_style="green"))


def report_failure(pr_link: str, verdict: str, root_cause: str, why: str,
                   steps: list, hist_errors: list, live_errors: list):
    """Display a failure report."""
    lines = [
        f"[bold red]❌ {verdict}[/bold red]\n",
        f"  PR:         {pr_link}",
        f"  Root cause: {root_cause or 'Could not determine'}",
    ]

    if why:
        lines.append(f"\n  [bold]Why unfixable:[/bold]\n    {why}")

    if steps:
        lines.append("\n  [bold]Steps attempted:[/bold]")
        for step in steps:
            desc = step.description if hasattr(step, 'description') else str(step)
            result = step.result if hasattr(step, 'result') else ""
            icon = "✔" if result == "success" else "✘" if result == "failed" else "→"
            lines.append(f"    {icon} {desc}")

    console.print(Panel("\n".join(lines), border_style="red"))


def script_generated(script_path: str, script_name: str):
    """Announce the generated fix script."""
    console.print(f"\n[bold green]✅ Script saved to: {script_path}[/bold green]")
    run_cmd = f"bash {script_name}"
    if _copy_to_clipboard(run_cmd):
        console.print(f"  [green]✔[/green] [dim]Run command copied to clipboard[/dim]")
