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
    progress_cancel, interrupted_msg,
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
    section("Tasks Folder (Historical Build Logs)")
    info("Point to the folder containing your historical build failure logs.")
    info("This folder will be scanned for .log, .txt, and other log files.")

    while True:
        tasks_path = user_prompt("Enter the path to your Tasks folder: ").strip()
        tasks_path = os.path.expanduser(tasks_path)

        if os.path.isdir(tasks_path):
            config.tasks_folder = tasks_path
            success(f"Tasks folder: {tasks_path}")
            break
        else:
            error(f"Directory not found: {tasks_path}")
            info("Please enter a valid directory path.")

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


def get_log_file_path() -> str:
    """Prompt for the Terminal A log file path (manual mode)."""
    info("In manual mode, we use the `script` command to record Terminal A output.")
    info("Press Enter to use the default log file location, or enter a custom path.")

    log_path = user_prompt("Log file path (or Enter for default): ").strip()
    if log_path:
        log_path = os.path.expanduser(log_path)
        # Create parent directory if needed
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        return log_path
    return ""  # Will use default


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

    if mode == "manual":
        log_file = get_log_file_path()
        bridge = TerminalBridge(project_path, log_file if log_file else None)

        info(f"Terminal A log file: {bridge.log_file}")

        manual = ManualMode(gemini, bridge, log_analyzer, pr_link, historical_context, output_dir)
        session = manual.run()

    elif mode == "auto":
        bridge = TerminalBridge(project_path)

        auto = AutoMode(gemini, bridge, log_analyzer, pr_link, historical_context, output_dir)
        session = auto.run()

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
