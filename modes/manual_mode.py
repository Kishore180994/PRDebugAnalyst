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

from agents.gemini_client import GeminiClient, GeminiAPIError
from agents.action_parser import ActionParser, ActionType
from utils.terminal_bridge import TerminalBridge
from utils.log_analyzer import LogAnalyzer
from utils.session_report import SessionState, SummaryParser, ReportGenerator
from utils.session_memory import SessionMemory
from utils.display import (
    section, success, error, warning, info, diminfo, agent_msg,
    user_prompt, command_display, commands_display, verdict_display,
    file_edit_preview,
    manual_mode_help, step_prompt, progress_spinner, progress_done,
    progress_cancel, interrupted_msg,
    thinking_start, tool_use, tool_result, work_start, work_end,
    report_success, report_failure, script_generated, session_stats,
)


class ManualMode:
    """Interactive manual mode where user executes commands in a separate terminal."""

    # Max auto-read files per agent response to prevent infinite chains
    MAX_AUTO_READS_PER_TURN = 5

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

        # Loop detection: track files already read and commands already suggested
        self._files_read: set[str] = set()
        self._commands_suggested: list[str] = []
        self._build_verified: bool = False  # Track if a build has been run and verified

        # Persistent session memory (survives history trimming)
        self._memory = SessionMemory()

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

        # Phase 1: Show historical analysis & start verify-first flow
        try:
            self._initial_analysis()
        except KeyboardInterrupt:
            progress_cancel()
            interrupted_msg()
            info("Initial analysis interrupted. You can still interact with the agent.")

        # Main interaction loop
        while self._is_running:
            try:
                self._interaction_loop()
            except KeyboardInterrupt:
                # Ctrl+C cancels the current turn, NOT the session
                progress_cancel()  # stop any active spinner
                interrupted_msg()
                # Stay in the loop — user can continue or type 'quit'
                continue
            except GeminiAPIError as e:
                progress_cancel()
                if not e.retryable:
                    error(f"API error (non-retryable): {e}")
                    info("This may require fixing your API key or configuration.")
                    info("Type 'quit' to exit, or fix the issue and press Enter.")
                else:
                    warning(f"API error: {e}")
                    info("The error may be temporary. Press Enter to retry, or type 'quit'.")
            except Exception as e:
                progress_cancel()
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
        commands = [a.content for a in actions if a.action_type == ActionType.COMMAND]

        if len(commands) == 1:
            command_display(commands[0], auto_copy=True)
        elif len(commands) > 1:
            commands_display(commands)

        for action in actions:
            if action.action_type == ActionType.VERDICT:
                self._handle_verdict(action.content, action.metadata.get("reason", ""), response)
                return

        self._step_count += 1

    def _interaction_loop(self):
        """Single iteration of the manual mode interaction loop."""
        raw_input = step_prompt(self._step_count)
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
            warning("No new logs found in Terminal A.")
            diminfo(f"Log file: {self.bridge.log_file}")
            diminfo("Make sure you started `script` in Terminal A first.")
            diminfo("Run the command in Terminal A, then press Enter here again.")
            return

        line_count = len(new_logs.splitlines())
        progress_done(f"{line_count} lines")

        # Only denoise if output is large (>100 lines). Short outputs are
        # passed through raw — every line matters when output is small.
        if line_count > 100:
            tool_use("Denoise", f"{line_count} raw lines")
            progress_spinner("Denoising logs")
            denoised = self.gemini.denoise_logs(new_logs)
            progress_done(f"{len(denoised.splitlines())} lines kept")
        else:
            denoised = new_logs
            diminfo(f"Short output ({line_count} lines) — skipping denoise, using raw logs")

        # Record observation from denoised logs
        build_status = "SUCCESS" if any(kw in denoised.lower() for kw in ["build successful", "build success"]) else "FAILED"
        self._memory.add_observation(f"Scanned logs (step {self._step_count}) — Build {build_status}")

        # Feed to main agent with session context
        thinking_start("analyzing build output")
        progress_spinner("Agent analyzing logs")

        # Inject session context at the top of the prompt
        context_block = self._memory.get_context_block()
        prompt = f"""{context_block}

Here are the latest denoised build logs from Terminal A (step {self._step_count}):

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

        # Track build verification
        if any(kw in denoised.lower() for kw in ["build failed", "build successful", "build_failed", "build success"]):
            self._build_verified = True
            self._memory.set_build_verified(True)

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

        self._memory.add_observation(f"Step {self._step_count} completed successfully (user confirmed)")

        # Inject session context
        context_block = self._memory.get_context_block()
        prompt = f"""{context_block}

Step {self._step_count} completed SUCCESSFULLY.
What should we do next? If the build is now passing, provide the full
SUMMARY block and declare VERDICT: BUILD_FIXED.
Otherwise, suggest the next command."""

        response = self.gemini.chat_main(prompt)
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

        self._memory.add_observation(f"Step {self._step_count} failed (user reported)")
        self._memory.add_error(f"Step {self._step_count} did not complete as expected")

        # Inject session context
        context_block = self._memory.get_context_block()
        prompt = f"""{context_block}

Step {self._step_count} FAILED. The command did not work as expected.
Please analyze what might have gone wrong and suggest an alternative approach.
If you believe this is unfixable without source code changes, provide the full
SUMMARY block and declare the appropriate VERDICT."""

        response = self.gemini.chat_main(prompt)
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

        # Inject session context with user message
        context_block = self._memory.get_context_block()
        prompt = f"""{context_block}

User message: {message}"""

        response = self.gemini.chat_main(prompt)
        progress_done()
        agent_msg(response)

        self.session.add_step(
            f"User message: {message[:60]}",
            result="info",
        )

        self._handle_agent_response(response)

    def _handle_agent_response(self, response: str):
        """
        Process the agent's response for actionable items.

        CRITICAL: This method NEVER recurses. After processing file reads
        and getting a new agent response, it displays the result and STOPS.
        Control always returns to the user after this method completes.
        """
        # Check for summary block
        agent_summary = SummaryParser.parse(response)
        if agent_summary:
            SummaryParser.merge_with_session(agent_summary, self.session)

        actions = ActionParser.parse(response)

        # ── Check for verdict first (terminal) ──
        for action in actions:
            if action.action_type == ActionType.VERDICT:
                self._handle_verdict(
                    action.content,
                    action.metadata.get("reason", ""),
                    response,
                )
                return

        # ── Collect and batch all file read requests ──
        file_reads = []
        for action in actions:
            if action.action_type == ActionType.READ_FILE:
                filepath = action.content
                # Loop detection: skip files we've already read
                if filepath in self._files_read:
                    diminfo(f"Skipping already-read file: {filepath}")
                    continue
                file_reads.append(filepath)

        # ── Read requested files in batch (no recursion!) ──
        if file_reads:
            # Cap to prevent runaway reads
            if len(file_reads) > self.MAX_AUTO_READS_PER_TURN:
                warning(f"Agent requested {len(file_reads)} files — capping at {self.MAX_AUTO_READS_PER_TURN}")
                file_reads = file_reads[:self.MAX_AUTO_READS_PER_TURN]

            file_contents = self._batch_read_files(file_reads)

            # Feed all file contents to agent in ONE message
            if file_contents:
                thinking_start("analyzing file contents")
                progress_spinner("Agent analyzing files")
                combined = "\n\n".join(file_contents)
                new_response = self.gemini.chat_main(
                    f"Here are the requested file contents:\n\n{combined}\n\n"
                    "Continue your analysis. Suggest the next command for me to run, "
                    "or provide a VERDICT if you have enough information."
                )
                progress_done()
                agent_msg(new_response)

                # Check the NEW response for summary/verdict/commands — but DO NOT recurse
                new_summary = SummaryParser.parse(new_response)
                if new_summary:
                    SummaryParser.merge_with_session(new_summary, self.session)

                new_actions = ActionParser.parse(new_response)

                for action in new_actions:
                    if action.action_type == ActionType.VERDICT:
                        self._handle_verdict(
                            action.content,
                            action.metadata.get("reason", ""),
                            new_response,
                        )
                        return

                # Display commands from the NEW response
                new_commands = [a.content for a in new_actions if a.action_type == ActionType.COMMAND]
                self._display_commands(new_commands)

                # Display any edit requests from the new response
                for action in new_actions:
                    if action.action_type == ActionType.EDIT_FILE:
                        self._apply_edit(action.content, action.metadata.get("new_content", ""))

                # If agent wants MORE files, just tell the user
                more_files = [a.content for a in new_actions if a.action_type == ActionType.READ_FILE]
                if more_files:
                    for f in more_files:
                        if f not in self._files_read:
                            info(f"Agent also wants: {f} (will read on next turn — press Enter)")

                # STOP HERE — return control to user
                self._trim_if_needed()
                return

        # ── Display commands from original response ──
        commands = [a.content for a in actions if a.action_type == ActionType.COMMAND]
        self._display_commands(commands)

        # ── Handle edit requests ──
        for action in actions:
            if action.action_type == ActionType.EDIT_FILE:
                self._apply_edit(action.content, action.metadata.get("new_content", ""))

        # Trim history if it's getting long
        self._trim_if_needed()

    def _display_commands(self, commands: list[str]):
        """Display commands with smart copy behavior. Track for loop detection."""
        if not commands:
            return

        # Loop detection: check if we're suggesting the same commands again
        for cmd in commands:
            if cmd in self._commands_suggested[-5:]:
                warning(f"Agent is re-suggesting a previous command: {cmd[:60]}...")

        self._commands_suggested.extend(commands)

        # Record commands in memory for session context
        for cmd in commands:
            self._memory.add_command(cmd, "suggested")

        if len(commands) == 1:
            command_display(commands[0], auto_copy=True)
        else:
            commands_display(commands)

    def _batch_read_files(self, filepaths: list[str]) -> list[str]:
        """Read multiple files and return formatted content strings."""
        results = []
        for filepath in filepaths:
            tool_use("Read", filepath)
            content = self.bridge.read_project_file(filepath)
            if content.startswith("[Error"):
                tool_result("Read", "error", content)
                results.append(f"File: {filepath}\n[Error: Could not read file]")
                # Record error in memory
                self._memory.add_error(f"Could not read {filepath}")
            else:
                tool_result("Read", "success", f"{len(content.splitlines())} lines")
                results.append(f"File: {filepath}\n```\n{content}\n```")
                # Record file read in memory with a summary
                summary = f"{len(content.splitlines())} lines"
                # Try to capture key content from first 100 chars
                first_lines = content.split("\n")[:3]
                if first_lines:
                    content_preview = " ".join(first_lines)[:80]
                    summary = f"{len(content.splitlines())} lines — {content_preview}"
                self._memory.add_file_read(filepath, summary)

            # Track that we've read this file
            self._files_read.add(filepath)
            self.session.add_step(
                f"Read file: {filepath}",
                result="success" if not content.startswith("[Error") else "failed",
                output_summary=f"{len(content.splitlines())} lines read" if not content.startswith("[Error") else "error",
            )
        return results

    def _trim_if_needed(self):
        """Compact conversation history if it's getting long, preserving session memory."""
        self.gemini.trim_history(
            keep_last_n=14,
            session_memory=self._memory if hasattr(self, '_memory') else None,
        )

    def _handle_verdict(self, verdict: str, reason: str, full_response: str):
        """Handle a verdict from the agent — display and finalize session."""
        # Build verification guard: don't accept BUILD_FIXED without actual build verification
        if verdict == "BUILD_FIXED" and not self._build_verified:
            warning("Agent declared BUILD_FIXED but no build output has been verified!")
            info("Please run the build command first and press Enter to scan the logs.")
            info("The agent's verdict will be recorded once the build is actually verified.")
            # Don't finalize — let the user continue
            return

        verdict_display(verdict, reason)
        self.session.finalize(verdict, reason)

        # Try to parse summary from the response
        agent_summary = SummaryParser.parse(full_response)
        if agent_summary:
            SummaryParser.merge_with_session(agent_summary, self.session)

        self._is_running = False

    def _apply_edit(self, filepath: str, new_content: str):
        """
        Auto-apply a build file edit. Shows the change but applies it
        automatically — the write_project_file whitelist ensures only
        build config files can be modified. User can revert via git.
        """
        tool_use("Write", filepath)
        file_edit_preview(filepath, new_content)

        ok = self.bridge.write_project_file(filepath, new_content)
        if ok:
            success(f"File written: {filepath}")
            self.session.files_changed.append({
                "file": filepath,
                "change": new_content[:100] + "..." if len(new_content) > 100 else new_content,
            })
            self.session.add_step(f"Wrote build file: {filepath}", result="success")
            change_desc = new_content[:80] if len(new_content) < 80 else new_content[:77] + "..."
            self._memory.add_file_edit(filepath, change_desc)
            self._memory.add_observation(f"Applied fix to {filepath}")

            # Notify agent — DON'T process response, user gets control
            progress_spinner("Notifying agent of edit")
            response = self.gemini.chat_main(
                f"Edit applied successfully to {filepath}. "
                "Now suggest running the build to verify the fix."
            )
            progress_done()
            agent_msg(response)
            actions = ActionParser.parse(response)
            commands = [a.content for a in actions if a.action_type == ActionType.COMMAND]
            self._display_commands(commands)
        else:
            error(f"Failed to write: {filepath} (not a build config file or permission denied)")
            self.session.add_step(f"Failed to write: {filepath}", result="failed")
            self._memory.add_error(f"Failed to write to {filepath}")

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

            # Generate .sh fix script (with memory context)
            try:
                script_path = ReportGenerator.generate_fix_script(self.session, self.output_dir, memory=self._memory)
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
