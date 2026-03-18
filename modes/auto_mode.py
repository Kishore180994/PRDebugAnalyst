"""
Auto Mode Handler for PR Debug Analyst.

In auto mode:
- Agent analyzes logs and decides commands to run
- Commands are executed automatically in the project directory
- Output is captured, denoised, and fed back to the agent
- Continues until a verdict is reached or max iterations exceeded

Workflow enforced:
  1. Show historical failures
  2. Run assembleDebug FIRST to verify live build matches historical
  3. Diagnose, then fix, then re-verify
  4. At the end: summary + .sh fix script (on success) or failure report
"""
import time
from typing import Optional

from agents.gemini_client import GeminiClient, GeminiAPIError
from agents.action_parser import ActionParser, ActionType
from utils.terminal_bridge import TerminalBridge
from utils.log_analyzer import LogAnalyzer
from utils.session_report import SessionState, SummaryParser, ReportGenerator
from utils.display import (
    section, success, error, warning, info, diminfo, agent_msg,
    verdict_display, progress_spinner, progress_done,
    progress_cancel, interrupted_msg, user_prompt,
    thinking_start, tool_use, tool_result, work_start, work_end,
    report_success, report_failure, script_generated, session_stats,
    file_edit_preview,
)


class AutoMode:
    """Autonomous mode where the agent drives the entire debugging process."""

    MAX_ITERATIONS = 25         # Safety limit on total iterations
    MAX_COMMANDS_PER_STEP = 3   # Max commands from a single agent response
    COMMAND_TIMEOUT = 600       # 10 min timeout per command

    def __init__(
        self,
        gemini: GeminiClient,
        bridge: TerminalBridge,
        log_analyzer: LogAnalyzer,
        pr_link: str,
        historical_context: str,
        output_dir: str = ".",
    ):
        self.gemini = gemini
        self.bridge = bridge
        self.log_analyzer = log_analyzer
        self.pr_link = pr_link
        self.historical_context = historical_context
        self.output_dir = output_dir
        self._iteration = 0
        self._verdict: Optional[tuple[str, str]] = None

        # Session tracking
        self.session = SessionState(
            pr_link=pr_link,
            mode="auto",
            project_path=bridge.project_path,
        )

    def run(self) -> SessionState:
        """
        Run the autonomous debugging loop.
        Returns the final SessionState (with verdict, summary, etc.).
        """
        section("Auto Mode Started")
        info(f"Project: {self.bridge.project_path}")
        info(f"Max iterations: {self.MAX_ITERATIONS}")
        print()

        # Phase 0: Show historical failures to user
        self._show_historical_summary()

        # Phase 1: Initial analysis — verify-first
        try:
            self._initial_analysis()
        except KeyboardInterrupt:
            progress_cancel()
            interrupted_msg()
            info("Initial analysis interrupted. Continuing to main loop.")

        # Phase 2: Iterative debugging loop
        work_start("Autonomous debugging")
        while self._verdict is None and self._iteration < self.MAX_ITERATIONS:
            self._iteration += 1
            diminfo(f"{'─' * 40}")
            diminfo(f"Iteration {self._iteration}/{self.MAX_ITERATIONS}")

            try:
                self._step()
            except KeyboardInterrupt:
                # Ctrl+C pauses auto mode — ask user what to do
                progress_cancel()
                interrupted_msg()
                action = self._pause_for_user()
                if action == "quit":
                    self._verdict = ("INTERRUPTED", "User chose to end the session.")
                    break
                elif action == "continue":
                    continue  # resume auto loop
                # else "skip" — just move to next iteration

            except GeminiAPIError as e:
                progress_cancel()
                self.session.add_step(
                    f"API error in iteration {self._iteration}",
                    result="failed",
                    output_summary=str(e),
                )
                if not e.retryable:
                    error(f"Non-retryable API error: {e}")
                    self._verdict = ("BUILD_UNFIXABLE_UNKNOWN", f"API error: {e}")
                    break
                else:
                    warning(f"API error: {e}")
                    if not self.gemini.is_healthy:
                        error("Too many consecutive API errors. Stopping.")
                        self._verdict = ("NEEDS_MORE_INVESTIGATION", f"Stopped due to repeated API errors: {e}")
                        break
                    info("Will retry on next iteration...")

            except Exception as e:
                progress_cancel()
                error(f"Error in iteration {self._iteration}: {e}")
                self.session.add_step(
                    f"Error in iteration {self._iteration}",
                    result="failed",
                    output_summary=str(e),
                )
                try:
                    response = self.gemini.chat_main(
                        f"An error occurred: {e}. Please adjust your approach. "
                        "If you cannot continue, provide the SUMMARY block and a VERDICT."
                    )
                    agent_msg(response)
                    self._check_verdict(response)
                except (GeminiAPIError, Exception):
                    self._verdict = ("BUILD_UNFIXABLE_UNKNOWN", f"Agent encountered unrecoverable error: {e}")

        work_end()

        # Max iterations reached without verdict
        if self._verdict is None:
            self._verdict = (
                "NEEDS_MORE_INVESTIGATION",
                f"Reached maximum iterations ({self.MAX_ITERATIONS}) without resolution."
            )

        # Finalize session
        self.session.finalize(self._verdict[0], self._verdict[1])
        verdict_display(self._verdict[0], self._verdict[1])

        # Generate final report
        self._generate_final_report()

        return self.session

    def _show_historical_summary(self):
        """Show the user what was failing historically before the agent starts."""
        section("Historical Failure Analysis")
        if self.historical_context and "No historical" not in self.historical_context:
            info("Here's what was failing in the historical logs for this PR:")
            print()
            for line in self.historical_context.split("\n"):
                print(f"    {line}")
            print()
            self.session.historical_errors = [
                line.strip() for line in self.historical_context.split("\n")
                if any(kw in line.lower() for kw in ["error", "fail", "task_failure", "exception"])
            ]
        else:
            warning("No historical logs found — starting from scratch.")

    def _initial_analysis(self):
        """Phase 1: Analyze historical logs, then run the build to verify live state."""
        thinking_start("analyzing PR and preparing verification build")
        progress_spinner("Phase 1: Analyzing PR and preparing verification build")

        build_files = self.log_analyzer.find_build_files(self.bridge.project_path)
        build_files_summary = "\n".join(f"  - {f}" for f in build_files[:20])

        initial_prompt = f"""I need you to autonomously debug a failed Android PR build.

PR Link: {self.pr_link}

We are in AUTO MODE. You can:
1. Suggest commands to run (I'll execute them and show you the output)
2. Request to read files (READ_FILE: <path>)
3. Request to edit build files (EDIT_FILE: <path> followed by content in a code block)
4. Declare a verdict when done (with the required SUMMARY block)

Build files found in the project:
{build_files_summary}

Historical build failure context:
{self.historical_context}

IMPORTANT — VERIFY-FIRST WORKFLOW:
1. FIRST, summarize what was failing in the historical logs
2. THEN, suggest running `./gradlew assembleDebug --stacktrace` to see the LIVE build errors
3. Do NOT suggest any fixes until we see the live build output
4. After seeing the live output, compare it with historical errors and then proceed to fix

RULES:
- Only edit build configuration files (gradle, properties, proguard, etc.)
- NEVER edit .java, .kt, .xml (layout), or other source files
- If source code changes are needed, provide SUMMARY block and declare BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED
- Track all steps — you must produce a SUMMARY block with your final VERDICT

Start now: summarize historical failures, then suggest the verification build command."""

        response = self.gemini.chat_main(initial_prompt)
        progress_done()
        agent_msg(response)

        self.session.add_step(
            "Initial analysis — reviewed historical logs, requested verification build",
            result="success",
        )

    def _step(self):
        """Execute one step of the autonomous loop."""
        last_response = self._get_last_agent_response()

        # Check for summary block in the response
        agent_summary = SummaryParser.parse(last_response)
        if agent_summary:
            SummaryParser.merge_with_session(agent_summary, self.session)

        actions = ActionParser.parse(last_response)
        commands_executed = 0

        for action in actions:
            if action.action_type == ActionType.VERDICT:
                self._verdict = (action.content, action.metadata.get("reason", ""))
                return

            elif action.action_type == ActionType.COMMAND:
                if commands_executed >= self.MAX_COMMANDS_PER_STEP:
                    warning(f"Skipping extra commands (limit: {self.MAX_COMMANDS_PER_STEP} per step)")
                    break
                self._execute_and_report(action.content)
                commands_executed += 1

            elif action.action_type == ActionType.READ_FILE:
                self._read_and_report(action.content)

            elif action.action_type == ActionType.EDIT_FILE:
                self._edit_and_report(action.content, action.metadata.get("new_content", ""))

            elif action.action_type == ActionType.MESSAGE:
                self._prompt_next_action()

        if commands_executed == 0 and not any(
            a.action_type in (ActionType.READ_FILE, ActionType.EDIT_FILE, ActionType.VERDICT)
            for a in actions
        ):
            self._prompt_next_action()

    def _execute_and_report(self, command: str):
        """Execute a command and report results to the agent."""
        tool_use("Bash", command)
        progress_spinner("Running command")

        return_code, output = self.bridge.execute_command_streaming(
            command, timeout=self.COMMAND_TIMEOUT
        )
        progress_done()

        status = "SUCCESS" if return_code == 0 else f"FAILED (exit code: {return_code})"
        tool_result("Bash", "success" if return_code == 0 else "error", status)

        # Denoise the output
        if len(output) > 200:
            tool_use("Denoise", f"{len(output.splitlines())} raw lines")
            progress_spinner("Denoising output")
            denoised = self.gemini.denoise_logs(output)
            progress_done(f"{len(denoised.splitlines())} lines kept")
        else:
            denoised = output

        # Track step
        self.session.add_step(
            f"Executed: {command[:80]}",
            command=command,
            result="success" if return_code == 0 else "failed",
            output_summary=denoised[:200],
        )

        # Report to agent with verify-aware prompting
        thinking_start("analyzing build output")
        progress_spinner("Agent analyzing results")
        response = self.gemini.chat_main(
            f"Command executed: `{command}`\n"
            f"Exit code: {return_code}\n"
            f"Status: {status}\n\n"
            f"Denoised output:\n```\n{denoised}\n```\n\n"
            "Analyze the output:\n"
            "1. If this was the verification build, compare errors with historical logs — do they match?\n"
            "2. If this was a fix verification, did the build pass now?\n"
            "3. Provide the next command, a file operation, or a VERDICT with SUMMARY block.\n"
            "4. If the build succeeded, provide SUMMARY block and VERDICT: BUILD_FIXED."
        )
        progress_done()
        agent_msg(response)

        self._check_verdict(response)

        if self.gemini.get_history_length() > 20:
            self.gemini.trim_history(keep_last_n=14)
            diminfo("(Trimmed conversation history to save context window)")

    def _read_and_report(self, filepath: str):
        """Read a file and report its contents to the agent."""
        tool_use("Read", filepath)
        content = self.bridge.read_project_file(filepath)

        if content.startswith("[Error"):
            tool_result("Read", "error", content)
        else:
            tool_result("Read", "success", f"{len(content.splitlines())} lines")

        self.session.add_step(
            f"Read file: {filepath}",
            result="success" if not content.startswith("[Error") else "failed",
            output_summary=f"{len(content.splitlines())} lines",
        )

        thinking_start("analyzing file contents")
        progress_spinner("Agent analyzing file")
        response = self.gemini.chat_main(
            f"Content of {filepath}:\n```\n{content}\n```\n\n"
            "Continue your analysis. What's next?"
        )
        progress_done()
        agent_msg(response)
        self._check_verdict(response)

    def _edit_and_report(self, filepath: str, new_content: str):
        """Edit a build file and report the result."""
        tool_use("Edit", filepath)
        file_edit_preview(filepath, new_content)

        ok = self.bridge.write_project_file(filepath, new_content)
        if ok:
            tool_result("Edit", "success", f"Updated {filepath}")
            self.session.files_changed.append({
                "file": filepath,
                "change": new_content[:100] + "..." if len(new_content) > 100 else new_content,
            })
            self.session.add_step(
                f"Edited build file: {filepath}",
                command=f"write {filepath}",
                result="success",
            )

            thinking_start("planning verification build")
            progress_spinner("Agent planning next step")
            response = self.gemini.chat_main(
                f"Successfully edited {filepath}. "
                "Now run the build again to verify the fix. "
                "Suggest the build command."
            )
            progress_done()
            agent_msg(response)
            self._check_verdict(response)
        else:
            tool_result("Edit", "error", f"Cannot edit {filepath} — not a build file or permission denied")
            self.session.add_step(f"Failed to edit: {filepath}", result="failed")

            thinking_start("adjusting approach")
            progress_spinner("Agent adjusting approach")
            response = self.gemini.chat_main(
                f"Could not edit {filepath}. It may not be a build configuration file. "
                "If this fix requires editing source code, provide SUMMARY block and declare "
                "BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED. Otherwise, suggest an alternative."
            )
            progress_done()
            agent_msg(response)
            self._check_verdict(response)

    def _prompt_next_action(self):
        """Ask the agent to provide the next concrete action."""
        thinking_start("deciding next action")
        progress_spinner("Requesting next action from agent")
        response = self.gemini.chat_main(
            "Please provide a concrete next step: "
            "either a command to run (in a ```bash block), "
            "a file to read (READ_FILE: <path>), "
            "a file to edit (EDIT_FILE: <path>), "
            "or a final VERDICT with SUMMARY block."
        )
        progress_done()
        agent_msg(response)
        self._check_verdict(response)

    def _check_verdict(self, response: str):
        """Check if the response contains a verdict, and extract summary."""
        # Check for summary block
        agent_summary = SummaryParser.parse(response)
        if agent_summary:
            SummaryParser.merge_with_session(agent_summary, self.session)

        result = ActionParser.extract_verdict(response)
        if result:
            self._verdict = result

    def _pause_for_user(self) -> str:
        """
        Called when Ctrl+C is pressed in auto mode.
        Gives the user a choice: continue, skip current step, or quit.
        Returns: 'continue', 'skip', or 'quit'.
        """
        info("Auto mode paused. What would you like to do?")
        info("  [c] Continue — resume auto mode")
        info("  [s] Skip — skip this step, move to next")
        info("  [q] Quit — end the session")
        print()

        while True:
            try:
                choice = user_prompt("Action (c/s/q): ").strip().lower()
            except KeyboardInterrupt:
                # Double Ctrl+C = quit
                print()
                return "quit"

            if choice in ("c", "continue", ""):
                info("Resuming auto mode...")
                return "continue"
            elif choice in ("s", "skip"):
                info("Skipping current step...")
                return "skip"
            elif choice in ("q", "quit"):
                return "quit"
            else:
                warning("Please enter c, s, or q.")

    def _get_last_agent_response(self) -> str:
        """Get the last agent (model) response from history."""
        for content in reversed(self.gemini._main_history):
            if content.role == "model" and content.parts:
                return content.parts[0].text or ""
        return ""

    # ── Final Reporting ─────────────────────────────────────────────────

    def _generate_final_report(self):
        """Generate the final summary, and .sh script if the build was fixed."""
        import os
        import datetime

        if self.session.verdict == "BUILD_FIXED":
            # ── Success path — polished report ──
            report_success(
                self.session.pr_link,
                self.session.root_cause,
                self.session.fix_applied,
                self.session.files_changed,
                self.session.steps,
            )

            # Generate .sh fix script
            try:
                script_path = ReportGenerator.generate_fix_script(self.session, self.output_dir)
                script_generated(script_path, os.path.basename(script_path))
            except Exception as e:
                warning(f"Could not generate fix script: {e}")

        elif self.session.verdict in (
            "BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED",
            "BUILD_UNFIXABLE_UNKNOWN",
            "NEEDS_MORE_INVESTIGATION",
        ):
            # ── Failure path — polished report ──
            report_failure(
                self.session.pr_link,
                self.session.verdict,
                self.session.root_cause,
                self.session.why_unfixable,
                self.session.steps,
                self.session.historical_errors,
                self.session.live_build_errors,
            )

        else:
            info(f"Session ended with status: {self.session.verdict or 'INCOMPLETE'}")

        # Show session stats
        duration = 0
        if self.session.started_at and self.session.ended_at:
            try:
                t1 = datetime.datetime.fromisoformat(self.session.started_at)
                t2 = datetime.datetime.fromisoformat(self.session.ended_at)
                duration = (t2 - t1).total_seconds()
            except Exception:
                pass
        session_stats(len(self.session.steps), self.gemini.get_history_length(), duration)

        # Save JSON report
        try:
            report_path = ReportGenerator.save_json_report(self.session, self.output_dir)
            diminfo(f"Report saved: {os.path.basename(report_path)}")
        except Exception as e:
            warning(f"Could not save session report: {e}")
