#!/usr/bin/env python3
"""
PR Debug Analyst - AI-powered Android build failure debugger.

Usage:
    python main.py

Environment:
    GEMINI_API_KEY  - Required. Your Google Gemini API key.
    MAIN_MODEL      - Optional. Override main agent model (default: gemini-3-flash-preview)
    DENOISER_MODEL  - Optional. Override denoiser model (default: gemini-2.5-flash)
"""
import os
import sys
import json
import datetime

from config import Config
from agents.gemini_client import GeminiClient
from utils.log_analyzer import LogAnalyzer
from utils.terminal_bridge import TerminalBridge
from utils.git_ops import parse_pr_link, setup_project_from_pr
from utils.display import (
    banner, section, success, error, warning, info,
    user_prompt, log_summary_table, verdict_display,
    progress_cancel, interrupted_msg, script_setup_display,
)
from modes.manual_mode import ManualMode
from modes.auto_mode import AutoMode


def setup_config() -> Config:
    """Interactive setup to gather all required configuration."""
    config = Config.from_env()

    # ── API Key ─────────────────────────────────────────────────────────
    if not config.gemini_api_key:
        section("API Key Setup")
        warning("No API key found.")
        info("Set GEMINI_API_KEY env var, or edit gemini_api_key in config.py.")
        key = user_prompt("Enter your Gemini API key: ").strip()
        if not key:
            error("API key is required.")
            sys.exit(1)
        config.gemini_api_key = key
    else:
        success("API key loaded")

    # ── Tasks Folder ────────────────────────────────────────────────────
    default_tasks = os.path.expanduser("~/Downloads/Tasks")

    section("Tasks Folder (Historical Build Logs)")
    info(f"Default: {default_tasks}")
    info("Press Enter to accept, or type a different path.")

    while True:
        raw = user_prompt(f"Tasks folder [{default_tasks}]: ").strip()
        tasks_path = os.path.expanduser(raw) if raw else default_tasks

        if os.path.isdir(tasks_path):
            config.tasks_folder = tasks_path
            success(f"Tasks folder: {tasks_path}")
            break
        else:
            # Try creating it
            try:
                os.makedirs(tasks_path, exist_ok=True)
                config.tasks_folder = tasks_path
                success(f"Created tasks folder: {tasks_path}")
                break
            except OSError:
                error(f"Directory not found and could not create: {tasks_path}")

    return config


def get_pr_link() -> str:
    """Prompt the user for the PR link."""
    section("PR Information")
    info("Enter the GitHub/GitLab PR link you want to analyze.")

    while True:
        pr_link = user_prompt("PR link: ").strip()
        if pr_link:
            success(f"PR: {pr_link}")
            return pr_link
        else:
            warning("Please enter a valid PR link.")


def get_project_path_manual() -> str:
    """Fallback: Prompt for the Android project path manually."""
    section("Android Project Path")
    info("Enter the path to the Android project directory.")
    info("This is where your project source and build files are located.")

    while True:
        project_path = user_prompt("Project path: ").strip()
        project_path = os.path.expanduser(project_path)

        if os.path.isdir(project_path):
            # Quick validation: check for build.gradle or settings.gradle
            has_gradle = any(
                os.path.exists(os.path.join(project_path, f))
                for f in ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"]
            )
            if has_gradle:
                success(f"Project: {project_path}")
            else:
                warning("No gradle files found at root - this may not be an Android project root.")
                info("Continuing anyway...")
            return project_path
        else:
            error(f"Directory not found: {project_path}")


def setup_project(pr_link: str) -> str:
    """
    Set up the project directory from a PR link.

    Flow:
    1. Parse the PR link to extract repo info
    2. Ask user for parent directory
    3. Clone or reuse existing repo
    4. Checkout the PR branch
    5. Fall back to manual path entry if anything fails
    """
    section("Project Setup")

    pr_info = parse_pr_link(pr_link)
    if pr_info:
        info(f"Detected repo: {pr_info.owner}/{pr_info.repo}  PR #{pr_info.pr_number}")
        project_path = setup_project_from_pr(pr_link)
        if project_path:
            # Validate it's an Android project
            has_gradle = any(
                os.path.exists(os.path.join(project_path, f))
                for f in ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"]
            )
            if has_gradle:
                success(f"Android project ready: {project_path}")
            else:
                warning("No gradle files found at project root — may not be an Android project.")
                info("Continuing anyway...")
            return project_path
        else:
            warning("Auto-setup failed. Falling back to manual entry.")
    else:
        warning("Could not parse repo info from PR link.")
        info("You can enter the project path manually.")

    return get_project_path_manual()


def get_log_file_path(pr_link: str) -> str:
    """
    Derive the terminal log file path from the PR link.
    Creates ~/.pr_terminal_logs/ and names the file after the repo.
    No user prompt needed — fully automatic.
    """
    log_dir = os.path.expanduser("~/.pr_terminal_logs")
    os.makedirs(log_dir, exist_ok=True)

    # Derive filename from PR link: owner__repo-pr_123_terminal.log
    pr_info = parse_pr_link(pr_link)
    if pr_info:
        log_name = f"{pr_info.owner}__{pr_info.repo}-pr_{pr_info.pr_number}_terminal.log"
    else:
        # Fallback: sanitize the PR link into a filename
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', pr_link.split('/')[-1])
        log_name = f"pr_{safe_name}_terminal.log"

    log_path = os.path.join(log_dir, log_name)

    # Clean up old log for this PR (fresh start each session)
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
        except OSError:
            pass

    return log_path


def select_mode() -> str:
    """Let the user choose between manual and auto mode."""
    section("Select Mode")
    print(f"""
  [1] Manual Mode
      - You run commands in Terminal A
      - Agent guides you step by step
      - Actions: Enter, done, fail, or type messages

  [2] Auto Mode
      - Agent runs everything autonomously
      - Commands are executed automatically
      - Hands-free until verdict
""")

    while True:
        choice = user_prompt("Select mode (1 or 2): ").strip()
        if choice in ("1", "manual"):
            success("Manual mode selected")
            return "manual"
        elif choice in ("2", "auto"):
            success("Auto mode selected")
            return "auto"
        else:
            warning("Please enter 1 or 2.")


def analyze_historical_logs(log_analyzer: LogAnalyzer, pr_link: str, gemini: GeminiClient) -> str:
    """
    Find and analyze historical logs SPECIFIC to the given PR.
    Uses AI-powered discovery to search through the dump in the Tasks folder.
    """
    section("Searching Historical Build Logs for This PR")
    info(f"Scanning tasks folder for logs matching: {pr_link}")

    # Phase 1+2: AI-powered PR-specific log discovery
    summaries = log_analyzer.find_logs_for_pr(pr_link, gemini)
    log_summary_table(summaries)

    if not summaries:
        warning("No historical logs found for this PR. Agent will work from scratch.")
        return "No historical build logs found for this specific PR."

    success(f"Found {len(summaries)} log file(s) for this PR")

    # Build a compact context string for the agent
    context_parts = []
    for s in summaries:
        part = f"Log: {s['filename']} (PR: {s['pr_id']})\n"
        part += f"  Path: {s.get('rel_path', s['filename'])}\n"
        if s.get("pr_refs_in_file"):
            part += f"  PR refs in file: {', '.join(s['pr_refs_in_file'][:5])}\n"
        if s.get("match_source"):
            part += f"  Match type: {s['match_source']}\n"
        if s["errors"]:
            errors_str = ", ".join(f"{e['type']}({e['count']}x)" for e in s["errors"])
            part += f"  Errors: {errors_str}\n"
        if s["failed_tasks"]:
            part += f"  Failed tasks: {', '.join(s['failed_tasks'])}\n"
        if s["error_snippet"]:
            snippet = s["error_snippet"][:800]
            part += f"  Error snippet:\n{snippet}\n"
        context_parts.append(part)

    return "\n---\n".join(context_parts)


def main():
    """Main entry point for PR Debug Analyst."""
    import argparse
    parser = argparse.ArgumentParser(description="PR Debug Analyst — AI-powered Android build debugger")
    parser.add_argument("--web", action="store_true", help="Launch live web dashboard alongside the terminal UI")
    parser.add_argument("--port", type=int, default=8420, help="Port for web dashboard (default: 8420)")
    args = parser.parse_args()

    banner()

    # ── Setup ───────────────────────────────────────────────────────────
    config = setup_config()

    pr_link = get_pr_link()
    project_path = setup_project(pr_link)
    mode = select_mode()

    # ── Initialize Components ───────────────────────────────────────────
    section("Initializing Agent")

    try:
        gemini = GeminiClient(config)
        success("Gemini client initialized")
        info(f"Main model: {config.main_model}")
        info(f"Denoiser model: {config.denoiser_model}")
    except Exception as e:
        error(f"Failed to initialize Gemini client: {e}")
        sys.exit(1)

    log_analyzer = LogAnalyzer(config.tasks_folder)

    # Output directory for reports and fix scripts
    output_dir = os.path.join(config.tasks_folder, "session_reports") if config.tasks_folder else "."

    # ── Historical Log Analysis (PR-specific) ───────────────────────────
    try:
        historical_context = analyze_historical_logs(log_analyzer, pr_link, gemini)
    except KeyboardInterrupt:
        progress_cancel()
        interrupted_msg()
        warning("Historical log search interrupted. Continuing without historical context.")
        historical_context = "Historical log search was interrupted by user."

    # ── Run Selected Mode ───────────────────────────────────────────────
    # Both modes now return a SessionState and handle their own final reports
    # (summary display, .sh script generation, JSON report saving)

    # ── Optional Web Dashboard ──
    web_url = None
    if args.web:
        try:
            from web.server import start_server
            web_url = start_server(
                port=args.port,
                log_file="",  # Will be set below for manual mode
                pr_link=pr_link,
                mode=mode,
            )
            success(f"Web dashboard: {web_url}")

            # Auto-open in browser
            import webbrowser
            try:
                webbrowser.open(web_url)
                info("Dashboard opened in browser")
            except Exception:
                info(f"Open in your browser: {web_url}")
        except ImportError as e:
            warning(f"Could not start web dashboard: {e}")
            info("Install with: pip install flask flask-socketio --break-system-packages")
        except Exception as e:
            warning(f"Web dashboard failed to start: {e}")

    if mode == "manual":
        log_file = get_log_file_path(pr_link)
        bridge = TerminalBridge(project_path, log_file)

        # Update web server with the log file path
        if args.web and web_url:
            try:
                from web import server as web_server
                web_server._log_file = bridge.log_file
            except Exception:
                pass

        # Show the script command prominently BEFORE anything else
        script_cmd = bridge.get_script_command()
        script_setup_display(script_cmd, bridge.log_file)
        user_prompt("Press Enter once you've started `script` in Terminal A...")
        success("Terminal A recording active — starting agent")

        # Reset read position to skip the script startup message
        bridge.reset_log_position()

        manual = ManualMode(gemini, bridge, log_analyzer, pr_link, historical_context, output_dir)
        session = manual.run()

    elif mode == "auto":
        bridge = TerminalBridge(project_path)

        auto = AutoMode(gemini, bridge, log_analyzer, pr_link, historical_context, output_dir)
        session = auto.run()

    # Stop web server if running
    if args.web:
        try:
            from web.server import stop_server
            stop_server()
        except Exception:
            pass

    section("Done")
    info("Thank you for using PR Debug Analyst!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        progress_cancel()
        print()
        info("Goodbye!")
        sys.exit(0)
