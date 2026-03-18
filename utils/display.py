"""
Display utilities for the PR Debug Analyst CLI interface.
Polished, Claude Code-style terminal UI with:
  - Animated spinners
  - Box-drawing layout
  - Markdown-aware agent message rendering
  - Gradient-like color accents
  - Clean, minimal design language
"""
import sys
import os
import re
import time
import threading
import shutil
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════
#  Color System — muted, professional palette inspired by Claude Code
# ═══════════════════════════════════════════════════════════════════════════

class C:
    """Compact color/style codes. Muted palette, not garish."""
    RST     = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    ITALIC  = "\033[3m"
    ULINE   = "\033[4m"
    STRIKE  = "\033[9m"

    # Foreground — 256-color for subtlety
    WHITE   = "\033[97m"
    GREY    = "\033[38;5;245m"
    DKGREY  = "\033[38;5;240m"
    BLACK   = "\033[30m"

    # Accent colors — softer tones
    BLUE    = "\033[38;5;75m"     # primary accent (like claude blue)
    CYAN    = "\033[38;5;116m"    # secondary accent
    GREEN   = "\033[38;5;114m"    # success
    RED     = "\033[38;5;203m"    # error
    YELLOW  = "\033[38;5;222m"    # warning
    ORANGE  = "\033[38;5;215m"    # highlight
    PURPLE  = "\033[38;5;141m"    # agent color
    PINK    = "\033[38;5;211m"    # emphasis

    # Background accents
    BG_DARK   = "\033[48;5;236m"
    BG_DARKER = "\033[48;5;234m"
    BG_BLUE   = "\033[48;5;24m"
    BG_GREEN  = "\033[48;5;22m"
    BG_RED    = "\033[48;5;52m"
    BG_YELLOW = "\033[48;5;58m"


def _term_width() -> int:
    """Get terminal width, default 80."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


# ═══════════════════════════════════════════════════════════════════════════
#  Animated Spinner
# ═══════════════════════════════════════════════════════════════════════════

class Spinner:
    """
    Animated terminal spinner that runs in a background thread.
    Usage:
        with Spinner("Thinking"):
            do_work()
    Or:
        spinner = Spinner("Analyzing"); spinner.start()
        ...
        spinner.stop()
    """
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = ""):
        self.message = message
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, final: str = ""):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        # Clear the spinner line
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()
        if final:
            sys.stdout.write(final + "\n")
            sys.stdout.flush()

    def _spin(self):
        i = 0
        while self._running:
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(
                f"\r  {C.BLUE}{frame}{C.RST} {C.GREY}{self.message}{C.RST}"
            )
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


# Global spinner instance for progress_spinner / progress_done pattern
_active_spinner: Optional[Spinner] = None


def progress_spinner(msg: str):
    """Start an animated spinner with a message."""
    global _active_spinner
    if _active_spinner:
        _active_spinner.stop()
    _active_spinner = Spinner(msg)
    _active_spinner.start()


def progress_done(label: str = "done"):
    """Stop the active spinner with a success label."""
    global _active_spinner
    if _active_spinner:
        _active_spinner.stop(f"  {C.GREEN}✓{C.RST} {C.GREY}{_active_spinner.message}{C.RST} {C.DIM}— {label}{C.RST}")
        _active_spinner = None


def progress_cancel():
    """Stop the active spinner with a 'cancelled' label (for Ctrl+C)."""
    global _active_spinner
    if _active_spinner:
        _active_spinner.stop(f"  {C.YELLOW}⊘{C.RST} {C.GREY}{_active_spinner.message}{C.RST} {C.DIM}— cancelled{C.RST}")
        _active_spinner = None


def interrupted_msg():
    """Show a clean 'interrupted' indicator when user presses Ctrl+C."""
    print(f"\n  {C.YELLOW}⊘{C.RST} {C.YELLOW}Interrupted{C.RST} — returning to prompt")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  Banner
# ═══════════════════════════════════════════════════════════════════════════

def banner():
    """Print the application banner — clean, compact, Claude Code-style."""
    w = min(_term_width(), 72)
    print()
    print(f"  {C.BLUE}{C.BOLD}PR Debug Analyst{C.RST}  {C.DKGREY}v1.0{C.RST}")
    print(f"  {C.DKGREY}AI-powered Android build failure debugger • Gemini{C.RST}")
    print(f"  {C.DKGREY}{'─' * (w - 4)}{C.RST}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  Section / Headers
# ═══════════════════════════════════════════════════════════════════════════

def section(title: str):
    """Print a clean section divider."""
    w = min(_term_width(), 72)
    print()
    print(f"  {C.BLUE}{C.BOLD}{'─' * 2} {title} {'─' * max(1, w - len(title) - 7)}{C.RST}")
    print()


def subsection(title: str):
    """Print a lighter subsection header."""
    print(f"\n  {C.CYAN}{title}{C.RST}")


# ═══════════════════════════════════════════════════════════════════════════
#  Status Messages
# ═══════════════════════════════════════════════════════════════════════════

def success(msg: str):
    print(f"  {C.GREEN}✓{C.RST} {msg}")

def error(msg: str):
    print(f"  {C.RED}✗{C.RST} {msg}")

def warning(msg: str):
    print(f"  {C.YELLOW}!{C.RST} {C.YELLOW}{msg}{C.RST}")

def info(msg: str):
    print(f"  {C.DKGREY}│{C.RST} {msg}")

def diminfo(msg: str):
    """Even more subtle info line."""
    print(f"  {C.DKGREY}│ {msg}{C.RST}")


# ═══════════════════════════════════════════════════════════════════════════
#  Agent Message — Markdown-aware rendering
# ═══════════════════════════════════════════════════════════════════════════

def agent_msg(msg: str):
    """
    Render the AI agent's message with lightweight markdown formatting.
    Handles: **bold**, `code`, ```code blocks```, headers, bullet points.
    """
    print()
    print(f"  {C.PURPLE}╭─{C.RST} {C.PURPLE}{C.BOLD}Agent{C.RST}")

    lines = msg.split("\n")
    in_code_block = False
    code_lang = ""

    for line in lines:
        # Code block fences
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
                lang_label = f" {C.DKGREY}{code_lang}{C.RST}" if code_lang else ""
                print(f"  {C.PURPLE}│{C.RST}  {C.BG_DARK} {lang_label}")
            else:
                in_code_block = False
                print(f"  {C.PURPLE}│{C.RST}  {C.BG_DARK}  {C.RST}")
            continue

        if in_code_block:
            # Code lines — monospace feel with dark background
            print(f"  {C.PURPLE}│{C.RST}  {C.BG_DARK} {C.GREEN}{line}{C.RST}")
            continue

        # Empty lines
        if not line.strip():
            print(f"  {C.PURPLE}│{C.RST}")
            continue

        # Render the line with inline formatting
        rendered = _render_inline_markdown(line)

        # SUMMARY_START/END — dim these
        if line.strip() in ("SUMMARY_START", "SUMMARY_END"):
            print(f"  {C.PURPLE}│{C.RST}  {C.DKGREY}{line.strip()}{C.RST}")
            continue

        # VERDICT/REASON lines — highlight
        if line.strip().startswith("VERDICT:"):
            verdict_val = line.strip()[8:].strip()
            vc = C.GREEN if "FIXED" in verdict_val else C.RED
            print(f"  {C.PURPLE}│{C.RST}  {vc}{C.BOLD}VERDICT: {verdict_val}{C.RST}")
            continue
        if line.strip().startswith("REASON:"):
            print(f"  {C.PURPLE}│{C.RST}  {C.GREY}REASON: {line.strip()[7:].strip()}{C.RST}")
            continue

        # Headers (## or **)
        stripped = line.strip()
        if stripped.startswith("#"):
            header_text = stripped.lstrip("#").strip()
            print(f"  {C.PURPLE}│{C.RST}  {C.BOLD}{C.WHITE}{header_text}{C.RST}")
            continue

        # Bullet points
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullet_text = _render_inline_markdown(stripped[2:])
            indent = len(line) - len(line.lstrip())
            pad = " " * (indent // 2)
            print(f"  {C.PURPLE}│{C.RST}  {pad}{C.BLUE}•{C.RST} {bullet_text}")
            continue

        # Numbered list
        num_match = re.match(r"^(\d+)\.\s+(.+)", stripped)
        if num_match:
            num = num_match.group(1)
            rest = _render_inline_markdown(num_match.group(2))
            print(f"  {C.PURPLE}│{C.RST}  {C.BLUE}{num}.{C.RST} {rest}")
            continue

        # Table rows (| col | col |)
        if stripped.startswith("|") and stripped.endswith("|"):
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                # Separator row
                print(f"  {C.PURPLE}│{C.RST}  {C.DKGREY}{stripped}{C.RST}")
            else:
                print(f"  {C.PURPLE}│{C.RST}  {C.GREY}{stripped}{C.RST}")
            continue

        # Summary block fields
        if re.match(r"^\s*(status|pr|root_cause|fix_applied|why_unfixable|steps_tried|files_changed):", stripped):
            key_match = re.match(r"^(\s*\S+:)\s*(.*)", stripped)
            if key_match:
                print(f"  {C.PURPLE}│{C.RST}  {C.DKGREY}{key_match.group(1)}{C.RST} {key_match.group(2)}")
                continue

        # Regular text
        print(f"  {C.PURPLE}│{C.RST}  {rendered}")

    print(f"  {C.PURPLE}╰─{C.RST}")
    print()


def _render_inline_markdown(text: str) -> str:
    """Apply inline markdown formatting: **bold**, `code`, *italic*."""
    # Bold: **text** or __text__
    text = re.sub(
        r"\*\*(.+?)\*\*",
        lambda m: f"{C.BOLD}{C.WHITE}{m.group(1)}{C.RST}",
        text,
    )
    # Inline code: `text`
    text = re.sub(
        r"`([^`]+)`",
        lambda m: f"{C.BG_DARK}{C.ORANGE} {m.group(1)} {C.RST}",
        text,
    )
    # Italic: *text* (but not **)
    text = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
        lambda m: f"{C.ITALIC}{m.group(1)}{C.RST}",
        text,
    )
    return text


# ═══════════════════════════════════════════════════════════════════════════
#  User Input
# ═══════════════════════════════════════════════════════════════════════════

def user_prompt(prompt_text: str = "") -> str:
    """Show a styled user input prompt."""
    if prompt_text:
        print(f"  {C.DKGREY}{prompt_text}{C.RST}")
    try:
        return input(f"  {C.BLUE}{C.BOLD}❯{C.RST} ")
    except EOFError:
        return "quit"


# ═══════════════════════════════════════════════════════════════════════════
#  Command Display — the box users copy-paste from
# ═══════════════════════════════════════════════════════════════════════════

def command_display(command: str, log_file: str = ""):
    """Display a command in a clean, copy-friendly box."""
    w = min(_term_width(), 72) - 6
    print()
    print(f"  {C.YELLOW}┌─{C.RST} {C.YELLOW}{C.BOLD}Run in Terminal A{C.RST}")
    print(f"  {C.YELLOW}│{C.RST}")
    print(f"  {C.YELLOW}│{C.RST}  {C.BG_DARK} {C.WHITE}{C.BOLD}  {command}  {C.RST}")
    print(f"  {C.YELLOW}│{C.RST}")
    if log_file:
        logged = f'({command}) 2>&1 | tee -a "{log_file}"'
        print(f"  {C.YELLOW}│{C.RST}  {C.DKGREY}with logging:{C.RST}")
        print(f"  {C.YELLOW}│{C.RST}  {C.DKGREY}{logged}{C.RST}")
        print(f"  {C.YELLOW}│{C.RST}")
    print(f"  {C.YELLOW}╰─{C.RST}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  Verdict Display
# ═══════════════════════════════════════════════════════════════════════════

def verdict_display(verdict: str, reason: str):
    """Display the final verdict in a prominent box."""
    is_success = verdict == "BUILD_FIXED"
    accent = C.GREEN if is_success else C.RED
    bg = C.BG_GREEN if is_success else C.BG_RED
    icon = "✅" if is_success else "❌"
    w = min(_term_width(), 72) - 4

    print()
    print(f"  {accent}{'━' * w}{C.RST}")
    print()
    print(f"  {accent}{C.BOLD}  {icon}  {verdict}{C.RST}")
    print()
    if reason:
        # Word-wrap reason
        words = reason.split()
        line = "  "
        for word in words:
            if len(line) + len(word) + 1 > w:
                print(f"  {C.GREY}{line}{C.RST}")
                line = "  "
            line += word + " "
        if line.strip():
            print(f"  {C.GREY}{line}{C.RST}")
    print()
    print(f"  {accent}{'━' * w}{C.RST}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  Log Summary Table
# ═══════════════════════════════════════════════════════════════════════════

def log_summary_table(log_summaries: list[dict]):
    """Display historical log summaries in a clean format."""
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

        # Header with badge
        badge = ""
        if match_source:
            badge = f"  {C.BG_DARK} {C.GREEN}{match_source}{C.RST}"
        print(f"  {C.BLUE}{C.BOLD}{i}.{C.RST} {C.WHITE}{C.BOLD}{pr_id}{C.RST}  {C.DKGREY}{rel_path}{C.RST}{badge}")

        # PR refs
        if pr_refs:
            refs_str = ", ".join(pr_refs[:3])
            print(f"     {C.DKGREY}refs: {refs_str}{C.RST}")

        # Errors
        if errors:
            for err in errors[:3]:
                print(f"     {C.RED}•{C.RST} {err['type']}  {C.DKGREY}×{err['count']}{C.RST}")
        else:
            print(f"     {C.DKGREY}no recognized error patterns{C.RST}")

        # Failed tasks
        if failed_tasks:
            tasks_str = ", ".join(failed_tasks[:5])
            print(f"     {C.YELLOW}failed:{C.RST} {tasks_str}")

        print()


# ═══════════════════════════════════════════════════════════════════════════
#  Manual Mode Help
# ═══════════════════════════════════════════════════════════════════════════

def manual_mode_help():
    """Display the manual mode action reference."""
    print()
    print(f"  {C.DKGREY}Actions:{C.RST}")
    print(f"  {C.BG_DARK} {C.WHITE}Enter{C.RST}  {C.RST} Scan logs → denoise → feed to agent")
    print(f"  {C.BG_DARK} {C.GREEN}done {C.RST}  {C.RST} Mark step as successful")
    print(f"  {C.BG_DARK} {C.RED}fail {C.RST}  {C.RST} Mark step as failed")
    print(f"  {C.BG_DARK} {C.YELLOW}quit {C.RST}  {C.RST} Exit session")
    print(f"  {C.DKGREY}Or type any message to chat with the agent{C.RST}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  Step indicator
# ═══════════════════════════════════════════════════════════════════════════

def step_prompt(step_num: int):
    """Display the step indicator before user input."""
    print(f"\n  {C.DKGREY}{'─' * 44}{C.RST}")
    print(f"  {C.BLUE}Step {step_num}{C.RST}  {C.DKGREY}enter · done · fail · quit · or type{C.RST}")


# ═══════════════════════════════════════════════════════════════════════════
#  File diff display (for edits)
# ═══════════════════════════════════════════════════════════════════════════

def file_edit_preview(filepath: str, content: str, max_lines: int = 15):
    """Show a preview of a file edit the agent wants to make."""
    print()
    print(f"  {C.ORANGE}┌─{C.RST} {C.ORANGE}{C.BOLD}Edit: {filepath}{C.RST}")
    lines = content.split("\n")
    shown = lines[:max_lines]
    for line in shown:
        print(f"  {C.ORANGE}│{C.RST}  {C.BG_DARK} {C.GREEN}{line}{C.RST}")
    if len(lines) > max_lines:
        print(f"  {C.ORANGE}│{C.RST}  {C.DKGREY}... +{len(lines) - max_lines} more lines{C.RST}")
    print(f"  {C.ORANGE}╰─{C.RST}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  Thinking Block — collapsible-style thinking indicator
# ═══════════════════════════════════════════════════════════════════════════

def thinking_start(topic: str = ""):
    """Show the start of a thinking/reasoning block (like Claude Code's thinking)."""
    label = f" {topic}" if topic else ""
    print(f"\n  {C.DKGREY}▸ thinking{label}...{C.RST}")


def thinking_end():
    """Close a thinking block."""
    pass  # The spinner stop handles the visual transition


def thinking_summary(text: str, max_lines: int = 3):
    """
    Show a collapsed summary of what the agent was thinking about.
    Like Claude Code's thinking block — dimmed, brief.
    """
    lines = text.strip().split("\n")[:max_lines]
    print(f"  {C.DKGREY}▸ reasoning{C.RST}")
    for line in lines:
        # Truncate long lines
        display_line = line[:80] + "..." if len(line) > 80 else line
        print(f"    {C.DKGREY}{display_line}{C.RST}")
    if len(text.strip().split("\n")) > max_lines:
        print(f"    {C.DKGREY}...{C.RST}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  Tool Use Indicators — like Claude Code's tool call display
# ═══════════════════════════════════════════════════════════════════════════

def tool_use(tool_name: str, detail: str = ""):
    """
    Show a tool being invoked, Claude Code style.
    E.g., tool_use("Read", "library/build.gradle")
          tool_use("Bash", "./gradlew assembleDebug")
          tool_use("Denoise", "47 lines → 12 lines")
    """
    detail_text = f"  {C.GREY}{detail}{C.RST}" if detail else ""
    print(f"  {C.CYAN}⬡{C.RST} {C.CYAN}{tool_name}{C.RST}{detail_text}")


def tool_result(tool_name: str, status: str = "success", detail: str = ""):
    """Show the result of a tool call."""
    if status == "success":
        icon = f"{C.GREEN}✓{C.RST}"
    elif status == "error":
        icon = f"{C.RED}✗{C.RST}"
    else:
        icon = f"{C.YELLOW}!{C.RST}"
    detail_text = f"  {C.DKGREY}{detail}{C.RST}" if detail else ""
    print(f"  {icon} {C.GREY}{tool_name}{C.RST}{detail_text}")


# ═══════════════════════════════════════════════════════════════════════════
#  Continuous Work Flow — shows the agent actively working
# ═══════════════════════════════════════════════════════════════════════════

def work_start(label: str = ""):
    """Visual separator indicating the agent is starting a work block."""
    suffix = f"  {C.DKGREY}{label}{C.RST}" if label else ""
    print(f"\n  {C.DKGREY}┌{'─' * 50}{C.RST}{suffix}")


def work_step(description: str):
    """A single step within a work block."""
    print(f"  {C.DKGREY}│{C.RST} {description}")


def work_end():
    """Close a work block."""
    print(f"  {C.DKGREY}└{'─' * 50}{C.RST}\n")


# ═══════════════════════════════════════════════════════════════════════════
#  Cost / Token display (like Claude Code's cost tracker)
# ═══════════════════════════════════════════════════════════════════════════

def session_stats(steps: int, turns: int, duration_sec: float = 0):
    """Show session statistics at the end, Claude Code style."""
    parts = [f"{C.DKGREY}steps: {steps}{C.RST}"]
    parts.append(f"{C.DKGREY}turns: {turns}{C.RST}")
    if duration_sec > 0:
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        parts.append(f"{C.DKGREY}time: {mins}m {secs}s{C.RST}")
    print(f"  {' · '.join(parts)}")
    print()


# ═══════════════════════════════════════════════════════════════════════════
#  Compact log line display
# ═══════════════════════════════════════════════════════════════════════════

def log_lines(raw_lines: str, max_lines: int = 8, label: str = ""):
    """Show a compact preview of log output."""
    lines = raw_lines.strip().split("\n")
    header = f"  {C.DKGREY}output" + (f" ({label})" if label else "") + f":{C.RST}"
    print(header)
    shown = lines[:max_lines]
    for line in shown:
        display = line[:90] + "..." if len(line) > 90 else line
        print(f"  {C.DKGREY}  {display}{C.RST}")
    if len(lines) > max_lines:
        print(f"  {C.DKGREY}  ... +{len(lines) - max_lines} more lines{C.RST}")


# ═══════════════════════════════════════════════════════════════════════════
#  Final report display (replaces plain text summaries)
# ═══════════════════════════════════════════════════════════════════════════

def report_success(pr_link: str, root_cause: str, fix: str, files: list[dict], steps: list):
    """Display a polished success report."""
    w = min(_term_width(), 72) - 4

    print(f"\n  {C.GREEN}{'━' * w}{C.RST}")
    print(f"  {C.GREEN}{C.BOLD}  ✅  BUILD FIXED{C.RST}")
    print(f"  {C.GREEN}{'━' * w}{C.RST}")
    print()
    print(f"  {C.GREY}PR{C.RST}          {pr_link}")
    print(f"  {C.GREY}Root cause{C.RST}  {root_cause}")
    print(f"  {C.GREY}Fix{C.RST}         {fix}")
    print()

    if files:
        print(f"  {C.GREY}Files changed:{C.RST}")
        for fc in files:
            print(f"    {C.GREEN}•{C.RST} {C.WHITE}{fc['file']}{C.RST}")
            print(f"      {C.DKGREY}{fc['change']}{C.RST}")
        print()

    if steps:
        print(f"  {C.GREY}Steps:{C.RST}")
        for i, step in enumerate(steps, 1):
            desc = step.description if hasattr(step, 'description') else str(step)
            result = step.result if hasattr(step, 'result') else ""
            icon = f"{C.GREEN}✓{C.RST}" if result == "success" else f"{C.RED}✗{C.RST}" if result == "failed" else f"{C.DKGREY}→{C.RST}"
            print(f"    {icon} {desc}")
        print()

    print(f"  {C.GREEN}{'━' * w}{C.RST}")


def report_failure(pr_link: str, verdict: str, root_cause: str, why: str, steps: list, hist_errors: list, live_errors: list):
    """Display a polished failure report."""
    w = min(_term_width(), 72) - 4

    print(f"\n  {C.RED}{'━' * w}{C.RST}")
    print(f"  {C.RED}{C.BOLD}  ❌  {verdict}{C.RST}")
    print(f"  {C.RED}{'━' * w}{C.RST}")
    print()
    print(f"  {C.GREY}PR{C.RST}          {pr_link}")
    print(f"  {C.GREY}Root cause{C.RST}  {root_cause or 'Could not determine'}")
    print()

    if hist_errors:
        print(f"  {C.GREY}Historical errors:{C.RST}")
        for e in hist_errors[:5]:
            print(f"    {C.DKGREY}•{C.RST} {e}")
        print()

    if live_errors:
        print(f"  {C.GREY}Live errors:{C.RST}")
        for e in live_errors[:5]:
            print(f"    {C.RED}•{C.RST} {e}")
        print()

    if steps:
        print(f"  {C.GREY}Steps attempted:{C.RST}")
        for i, step in enumerate(steps, 1):
            desc = step.description if hasattr(step, 'description') else str(step)
            result = step.result if hasattr(step, 'result') else ""
            icon = f"{C.GREEN}✓{C.RST}" if result == "success" else f"{C.RED}✗{C.RST}" if result == "failed" else f"{C.DKGREY}→{C.RST}"
            cmd = step.command if hasattr(step, 'command') and step.command else ""
            print(f"    {icon} {desc}")
            if cmd:
                print(f"      {C.DKGREY}$ {cmd[:70]}{C.RST}")
        print()

    if why:
        print(f"  {C.GREY}Why unfixable:{C.RST}")
        # Word-wrap
        words = why.split()
        line = "    "
        for word in words:
            if len(line) + len(word) + 1 > w:
                print(f"  {line}")
                line = "    "
            line += word + " "
        if line.strip():
            print(f"  {line}")
        print()

    print(f"  {C.RED}{'━' * w}{C.RST}")


def script_generated(script_path: str, script_name: str):
    """Announce the generated fix script."""
    print()
    print(f"  {C.GREEN}⬡{C.RST} {C.GREEN}{C.BOLD}Fix script generated{C.RST}")
    print(f"    {C.WHITE}{script_name}{C.RST}")
    print(f"    {C.DKGREY}cd /path/to/project && bash {script_name}{C.RST}")
    print()
