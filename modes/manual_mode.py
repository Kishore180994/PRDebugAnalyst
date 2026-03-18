"""
Manual Mode Handler for PR Debug Analyst.

In manual mode:
- Agent suggests commands to run in Terminal A
- User runs them manually and presses Enter to continue
- Terminal B (this script) reads the logs, denoises them, feeds to agent
- User can type 'done', 'fail', or free-text to communicate with agent

Workflow enforced:
  1. Show historical failures to user
  2. Run assembleDebug FIRST to verify live build matches historical
  3. Only then proceed to diagnosis and fixes
  4. At the end: summary + .sh fix script (on success) or failure report
"""
import sys
import time

from agents.gemini_client import GeminiClient
from agents.action_parser import ActionParser, ActionType
from utils.terminal_bridge import TerminalBridge
from utils.log_analyzer import LogAnalyzer
from utils.session_report import SessionState, SummaryParser, ReportGenerator
from utils.display import (
    section, success, error, warning, info, diminfo, agent_msg,
    user_prompt, command_display, verdict_display, file_edit_preview,
    manual_mode_help, step_prompt, progress_spinner, progress_done,
    thinking_start, tool_use, tool_result, work_start, work_end,
    report_success, report_failure, script_generated, session_stats,
)


class ManualMode:
    """Interactive manual mode where user executes commands in a separate terminal."""

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
        self._step_count = 0
        self._is_running = True

        # Session tracking
        self.session = SessionState(
            pr_link=pr_link,
            mode="manual",
            project_path=bridge.project_path,
        )

    def run(self) -> SessionState:
        """Main loop for manual mode. Returns the final session state."""
        section("Manual Mode Started")
        manual_mode_help()

        info(f"Log file: {self.bridge.log_file}")
        info("Make sure Terminal A outputs are piped to this log file.")
        print()

        # Phase 1: Show historical analysis & start verify-first flow
        self._initial_analysis()

        # Main interaction loop
        while self._is_running:
            try:
                self._interaction_loop()
            except KeyboardInterrupt:
                print()
                warning("Session interrupted by user.")
                self.session.finalize("INTERRUPTED", "User interrupted the session.")
                self._is_running = False
            except Exception as e:
                error(f"Error: {e}")
                info("Type 'quit' to exit or press Enter to continue.")

        # Final reporting
        self._generate_final_report()

        return self.session

    def _initial_analysis(self):
        """
        Phase 1: Show what was failing historically, then tell agent to run
        the build FIRST before suggesting any fixes.
        """
        # ── Show historical failures to user directly ──
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

        # ── Send to agent with verify-first instruction ──
        progress_spinner("Preparing initial analysis")

        initial_prompt = f"""I need your help debugging a failed Android PR build.

PR Link: {self.pr_link}

We are in MANUAL MODE. I will run commands you suggest in a separate terminal.
After running each command, I'll feed you the output logs (denoised).

Here is the historical build failure context from our logs:
{self.historical_context}

IMPORTANT: Follow the verify-first workflow:
1. FIRST, tell me what was failing based on the historical logs (summarize the errors)
2. THEN, suggest running the build command (assembleDebug) so we can see the LIVE error
3. Do NOT suggest any fixes yet — we need to verify the live build first

Remember: We ONLY fix build configuration files. If project source code changes are needed,
declare BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED."""

        response = self.gemini.chat_main(initial_prompt)
        progress_done()

        agent_msg(response)

        # Record step
        self.session.add_step(
            "Initial analysis — reviewed historical logs",
            result="success",
            output_summary="Historical errors identified, build verification requested",
        )

        # Parse and display any commands
        actions = ActionParser.parse(response)
        for action in actions:
            if action.action_type == ActionType.COMMAND:
                command_display(action.content, self.bridge.log_file)
            elif action.action_type == ActionType.VERDICT:
                self._handle_verdict(action.content, action.metadata.get("reason", ""), response)
                return

        self._step_count += 1

    def _interaction_loop(self):
        """Single iteration of the manual mode interaction loop."""
        step_prompt(self._step_count)
        raw_input = user_prompt()
        user_input = raw_input.strip().lower()

        if user_input == "quit":
            self.session.finalize("QUIT", "User quit the session.")
            self._is_running = False
            return

        elif user_input == "" or user_input == "enter":
            self._scan_and_continue()

        elif user_input == "done":
            self._handle_done()

        elif user_input == "fail":
            self._handle_fail()

        else:
            self._send_user_message(raw_input.strip())

    def _scan_and_continue(self):
        """Read new logs from Terminal A, denoise, and feed to agent."""
        tool_use("Read", f"Terminal A logs → {self.bridge.log_file}")
        progress_spinner("Reading logs from Terminal A")
        new_logs = self.bridge.read_new_logs()

        if not new_logs.strip():
            progress_done("no new content")
            warning("No new logs found. Make sure Terminal A output is being logged.")
            diminfo(f"Expected: {self.bridge.log_file}")
            diminfo("You can also paste log content directly as a message.")
            return

        progress_done(f"{len(new_logs.splitlines())} lines")

        # Denoise the logs
        tool_use("Denoise", f"{len(new_logs.splitlines())} raw lines")
        progress_spinner("Denoising logs")
        denoised = self.gemini.denoise_logs(new_logs)
        progress_done(f"{len(denoised.splitlines())} lines kept")

        # Feed to main agent
        thinking_start("analyzing build output")
        progress_spinner("Agent analyzing logs")
        prompt = f"""Here are the latest denoised build logs from Terminal A (step {self._step_count}):

```
{denoised}
```

Analyze these logs and tell me:
1. What happened (success/failure)?
2. If this is the first build run, compare these errors with the historical logs — do they match?
3. What is the root cause if it failed?
4. What should I do next?

If the build SUCCEEDED, provide the final SUMMARY block and VERDICT: BUILD_FIXED.
If suggesting a fix, explain what you're fixing and why.
If you've reached a conclusion that this is unfixable, provide the SUMMARY block and appropriate VERDICT."""

        response = self.gemini.chat_main(prompt)
        progress_done()

        agent_msg(response)

        # Track step
        self.session.add_step(
            f"Scanned Terminal A logs (step {self._step_count})",
            result="success",
            output_summary=denoised[:200],
        )

        self._handle_agent_response(response)
        self._step_count += 1

    def _handle_done(self):
        """User reports the last step was successful."""
        thinking_start("step succeeded — planning next")
        progress_spinner("Reporting success to agent")
        response = self.gemini.chat_main(
            f"Step {self._step_count} completed SUCCESSFULLY. "
            "What should we do next? If the build is now passing, provide the full "
            "SUMMARY block and declare VERDICT: BUILD_FIXED. "
            "Otherwise, suggest the next command."
        )
        progress_done()
        agent_msg(response)

        self.session.add_step(
            f"Step {self._step_count} — user reported success",
            result="success",
        )

        self._handle_agent_response(response)
        self._step_count += 1

    def _handle_fail(self):
        """User reports the last step failed."""
        thinking_start("step failed — adjusting approach")
        progress_spinner("Reporting failure to agent")
        response = self.gemini.chat_main(
            f"Step {self._step_count} FAILED. The command did not work as expected. "
            "Please analyze what might have gone wrong and suggest an alternative approach. "
            "If you believe this is unfixable without source code changes, provide the full "
            "SUMMARY block and declare the appropriate VERDICT."
        )
        progress_done()
        agent_msg(response)

        self.session.add_step(
            f"Step {self._step_count} — user reported failure",
            result="failed",
        )

        self._handle_agent_response(response)
        self._step_count += 1

    def _send_user_message(self, message: str):
        """Send a free-text message from the user to the agent."""
        thinking_start("processing message")
        progress_spinner("Agent processing your message")
        response = self.gemini.chat_main(f"User message: {message}")
        progress_done()
        agent_msg(response)

        self.session.add_step(
            f"User message: {message[:60]}",
            result="info",
        )

        self._handle_agent_response(response)

    def _handle_agent_response(self, response: str):
        """Process the agent's response for any actionable items."""
        # Check for summary block
        agent_summary = SummaryParser.parse(response)
        if agent_summary:
            SummaryParser.merge_with_session(agent_summary, self.session)

        actions = ActionParser.parse(response)

        for action in actions:
            if action.action_type == ActionType.COMMAND:
                command_display(action.content, self.bridge.log_file)

            elif action.action_type == ActionType.READ_FILE:
                info(f"Agent requested file: {action.content}")
                self._auto_read_file(action.content)

            elif action.action_type == ActionType.EDIT_FILE:
                self._confirm_edit(action.content, action.metadata.get("new_content", ""))

            elif action.action_type == ActionType.VERDICT:
                self._handle_verdict(
                    action.content,
                    action.metadata.get("reason", ""),
                    response,
                )

        # Trim history if it's getting long
        if self.gemini.get_history_length() > 20:
            self.gemini.trim_history(keep_last_n=14)
            info("(Trimmed conversation history to save context window)")

    def _handle_verdict(self, verdict: str, reason: str, full_response: str):
        """Handle a verdict from the agent — display and finalize session."""
        verdict_display(verdict, reason)
        self.session.finalize(verdict, reason)

        # Try to parse summary from the response
        agent_summary = SummaryParser.parse(full_response)
        if agent_summary:
            SummaryParser.merge_with_session(agent_summary, self.session)

        self._is_running = False

    def _auto_read_file(self, filepath: str):
        """Automatically read a file and feed it to the agent."""
        tool_use("Read", filepath)
        content = self.bridge.read_project_file(filepath)
        if content.startswith("[Error"):
            tool_result("Read", "error", content)
            self.gemini.chat_main(f"Could not read file {filepath}: {content}")
        else:
            tool_result("Read", "success", f"{len(content.splitlines())} lines")
            thinking_start("analyzing file contents")
            progress_spinner("Agent analyzing file")
            response = self.gemini.chat_main(
                f"Here is the content of {filepath}:\n```\n{content}\n```\n"
                "Continue your analysis."
            )
            progress_done()
            agent_msg(response)

            self.session.add_step(
                f"Read file: {filepath}",
                result="success",
                output_summary=f"{len(content.splitlines())} lines read",
            )

            self._handle_agent_response(response)

    def _confirm_edit(self, filepath: str, new_content: str):
        """Ask user to confirm a build file edit."""
        tool_use("Edit", filepath)
        file_edit_preview(filepath, new_content)
        confirm = user_prompt("Apply this edit? (yes/no): ").strip().lower()

        if confirm in ("yes", "y"):
            ok = self.bridge.write_project_file(filepath, new_content)
            if ok:
                success(f"File updated: {filepath}")
                self.session.files_changed.append({
                    "file": filepath,
                    "change": new_content[:100] + "..." if len(new_content) > 100 else new_content,
                })
                self.session.add_step(
                    f"Edited build file: {filepath}",
                    result="success",
                )
                self.gemini.chat_main(f"Edit applied successfully to {filepath}. What's next?")
            else:
                error(f"Failed to write: {filepath}")
                self.session.add_step(f"Failed to edit: {filepath}", result="failed")
                self.gemini.chat_main(f"Failed to write to {filepath}. File may not be a build file.")
        else:
            info("Edit skipped by user.")
            self.session.add_step(f"Edit declined by user: {filepath}", result="skipped")
            self.gemini.chat_main(f"User declined the edit to {filepath}. Suggest alternatives.")

    # ── Final Reporting ─────────────────────────────────────────────────

    def _generate_final_report(self):
        """Generate the final summary, and .sh script if the build was fixed."""
        import os

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
        import datetime
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
