"""
Gemini API client wrapper for the PR Debug Analyst.
Handles both the main reasoning agent and the fast denoiser agent.
Includes retry logic, rate-limit handling, and graceful error recovery.
"""
import json
import os
import time
from typing import Optional
from google import genai
from google.genai import types

from config import Config


class GeminiAPIError(Exception):
    """Raised when a Gemini API call fails after retries."""

    def __init__(self, message: str, retryable: bool = False, original: Exception = None):
        super().__init__(message)
        self.retryable = retryable
        self.original = original


class GeminiClient:
    """Wrapper around Gemini API with conversation history management."""

    # Retry config
    MAX_RETRIES = 3
    RETRY_DELAYS = [2, 5, 15]  # seconds between retries (exponential-ish)

    # Error classification
    RETRYABLE_KEYWORDS = [
        "rate limit", "quota", "429", "503", "500", "502",
        "overloaded", "temporarily", "unavailable", "timeout",
        "resource_exhausted", "deadline", "internal",
        "connection", "reset", "broken pipe", "eof",
    ]

    NON_RETRYABLE_KEYWORDS = [
        "invalid api key", "api key not valid", "permission denied",
        "not found", "404", "invalid argument", "blocked",
        "safety", "harm", "recitation",
    ]

    def __init__(self, config: Config):
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)
        self._main_history: list[types.Content] = []
        self._token_count: int = 0
        self._consecutive_errors: int = 0

    # ── Error Classification ─────────────────────────────────────────────

    def _classify_error(self, error: Exception) -> tuple[bool, str]:
        """
        Classify an error as retryable or not.
        Returns (is_retryable, friendly_message).
        """
        err_str = str(error).lower()

        # Check non-retryable first (more specific)
        for kw in self.NON_RETRYABLE_KEYWORDS:
            if kw in err_str:
                if "api key" in err_str:
                    return False, "Invalid API key. Check your GEMINI_API_KEY."
                if "blocked" in err_str or "safety" in err_str:
                    return False, "Request was blocked by safety filters."
                if "permission" in err_str:
                    return False, "Permission denied. Check API key permissions."
                return False, f"Non-retryable error: {error}"

        # Check retryable
        for kw in self.RETRYABLE_KEYWORDS:
            if kw in err_str:
                if "rate limit" in err_str or "429" in err_str or "quota" in err_str:
                    return True, "Rate limited — waiting before retry."
                if "503" in err_str or "overloaded" in err_str or "unavailable" in err_str:
                    return True, "Server overloaded — waiting before retry."
                if "500" in err_str or "internal" in err_str:
                    return True, "Server error — waiting before retry."
                if "timeout" in err_str or "deadline" in err_str:
                    return True, "Request timed out — will retry."
                if "connection" in err_str or "reset" in err_str or "eof" in err_str:
                    return True, "Connection error — will retry."
                return True, f"Temporary error — will retry: {error}"

        # Default: treat as retryable for the first few attempts
        return True, f"Unexpected error: {error}"

    # ── Retry Wrapper ────────────────────────────────────────────────────

    def _call_with_retry(self, call_fn, context: str = "API call") -> any:
        """
        Execute a Gemini API call with retry logic.
        call_fn: a zero-arg callable that makes the API call and returns the response.
        context: label for error messages (e.g., "main agent", "denoiser").
        """
        from utils.display import warning, diminfo

        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                result = call_fn()
                # Success — reset consecutive error counter
                self._consecutive_errors = 0
                return result

            except KeyboardInterrupt:
                raise  # Never swallow Ctrl+C

            except Exception as e:
                last_error = e
                is_retryable, friendly_msg = self._classify_error(e)

                if not is_retryable:
                    raise GeminiAPIError(friendly_msg, retryable=False, original=e) from e

                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                    warning(f"{friendly_msg}")
                    diminfo(f"Retry {attempt + 1}/{self.MAX_RETRIES} in {delay}s...")
                    time.sleep(delay)
                else:
                    self._consecutive_errors += 1

        # All retries exhausted
        raise GeminiAPIError(
            f"{context} failed after {self.MAX_RETRIES} retries: {last_error}",
            retryable=True,
            original=last_error,
        )

    # ── Main Agent ──────────────────────────────────────────────────────

    def chat_main(self, user_message: str, system_instruction: str = "") -> str:
        """
        Send a message to the main reasoning agent (Gemini 3 Flash).
        Maintains conversation history for multi-turn context.
        Retries on transient errors; raises GeminiAPIError on persistent failure.
        """
        self._main_history.append(
            types.Content(role="user", parts=[types.Part(text=user_message)])
        )

        def _do_call():
            response = self.client.models.generate_content(
                model=self.config.main_model,
                contents=self._main_history,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction or self._default_system_prompt(),
                    temperature=0.2,
                    max_output_tokens=8192,
                ),
            )
            return response.text or ""

        try:
            assistant_text = self._call_with_retry(_do_call, context="Main agent")
            self._main_history.append(
                types.Content(role="model", parts=[types.Part(text=assistant_text)])
            )
            return assistant_text

        except (GeminiAPIError, Exception) as e:
            # Remove the failed user message so history stays consistent
            if self._main_history and self._main_history[-1].role == "user":
                self._main_history.pop()
            raise GeminiAPIError(
                f"Main agent error: {e}",
                retryable=getattr(e, 'retryable', False),
                original=getattr(e, 'original', e),
            ) from e

    # ── Denoiser Agent ──────────────────────────────────────────────────

    def denoise_logs(self, raw_logs: str) -> str:
        """
        Use the fast denoiser model (Gemini 2.5 Flash) to clean build logs.
        This is a stateless call - no history maintained.
        Strips noise but PRESERVES all actual log lines verbatim — no summarizing.
        Falls back to raw logs if all retries fail.
        """
        prompt = f"""You are a log denoising agent. Remove ONLY obvious noise. Be CONSERVATIVE — when in doubt, KEEP the line.

REMOVE (clearly noise):
- ANSI escape codes and color sequences
- Download progress bars (e.g., [===>   ] 45%)
- Repeated identical lines (keep first occurrence)
- Gradle daemon startup messages
- File download percentage updates

KEEP (important — do NOT remove):
- ALL error messages, warnings, "BUILD FAILED", "BUILD SUCCESSFUL", "What went wrong"
- ALL task names (":app:compileDebugKotlin FAILED")
- ALL dependency resolution messages, file paths, compiler errors with line numbers
- ALL stack trace frames (full traces, do not truncate)
- Build config output (SDK versions, Java versions, Gradle version)
- Command prompts and commands run ($ ./gradlew ...)

Output cleaned lines EXACTLY as they appear. No paraphrasing, no commentary, no analysis. When in doubt, KEEP IT.
If build succeeded with no issues, respond with "NO_BUILD_ISSUES_DETECTED".

--- RAW TERMINAL LOGS ---
{raw_logs}"""

        def _do_call():
            response = self.client.models.generate_content(
                model=self.config.denoiser_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=8192,
                ),
            )
            return response.text or raw_logs

        try:
            return self._call_with_retry(_do_call, context="Denoiser")
        except (GeminiAPIError, Exception) as e:
            from utils.display import warning
            warning(f"Denoiser failed, using raw logs: {e}")
            return raw_logs

    # ── Health Check ─────────────────────────────────────────────────────

    @property
    def is_healthy(self) -> bool:
        """Check if the client is in a healthy state (not too many consecutive errors)."""
        return self._consecutive_errors < 5

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    # ── History Management ──────────────────────────────────────────────

    def reset_history(self):
        """Clear the main agent's conversation history."""
        self._main_history.clear()
        self._token_count = 0

    def get_history_length(self) -> int:
        """Return number of turns in the main agent's history."""
        return len(self._main_history)

    # ── History Compaction (PRFAgent-style) ─────────────────────────────

    # When total history text exceeds this, compact older turns.
    HISTORY_COMPACT_THRESHOLD = 500_000  # ~125k tokens
    HISTORY_KEEP_RECENT = 6  # last 3 user/model exchanges

    def _estimate_history_chars(self) -> int:
        """Estimate total character count in conversation history."""
        total = 0
        for item in self._main_history:
            for p in item.parts:
                if hasattr(p, 'text') and p.text:
                    total += len(p.text)
        return total

    def trim_history(self, keep_last_n: int = 10, session_memory=None):
        """
        Smart history compaction with LLM summarization.

        Instead of naively dropping old turns (losing step info needed for
        the final fix script), this:
        1. Separates file contents (preserved verbatim) from conversation
        2. Uses the denoiser model to compress conversation into a briefing
        3. Injects session memory as structured context
        4. Keeps the last N turns verbatim

        Falls back to naive trimming if LLM compaction fails.
        """
        estimated = self._estimate_history_chars()
        if estimated < self.HISTORY_COMPACT_THRESHOLD:
            return

        from utils.display import info, warning, progress_spinner, progress_done

        info(f"History compaction ({estimated:,} chars > {self.HISTORY_COMPACT_THRESHOLD:,} threshold)")

        keep_count = min(self.HISTORY_KEEP_RECENT, len(self._main_history))
        old_items = self._main_history[:-keep_count] if keep_count else self._main_history[:]
        recent_items = self._main_history[-keep_count:] if keep_count else []

        if not old_items:
            return

        # Separate file data from conversation
        file_data_parts = []
        conversation_parts = []

        for item in old_items:
            role = item.role.upper() if hasattr(item, 'role') else "UNKNOWN"
            for p in item.parts:
                if hasattr(p, 'text') and p.text:
                    text = p.text
                    if text.startswith("--- FILE:") or "Here is the content of" in text[:100]:
                        if len(text) > 10_000:
                            text = text[:5000] + "\n...[TRUNCATED]...\n" + text[-5000:]
                        file_data_parts.append(text)
                    else:
                        snippet = text[:2000] + ("…" if len(text) > 2000 else "")
                        conversation_parts.append(f"[{role}] {snippet}")

        conversation_digest = "\n".join(conversation_parts)
        if len(conversation_digest) > 60_000:
            conversation_digest = conversation_digest[:30_000] + "\n...[MIDDLE TRUNCATED]...\n" + conversation_digest[-30_000:]

        # Compress conversation via LLM
        summary_prompt = (
            "You are a context-compaction assistant. Below is the CONVERSATION portion "
            "of an Android build debugging session. File contents are preserved separately.\n\n"
            "Condense into a structured briefing preserving:\n"
            "- Key errors and stack traces found\n"
            "- Steps attempted and outcomes (INCLUDE EXACT COMMANDS)\n"
            "- Current diagnosis / working hypothesis\n"
            "- Files modified and what was changed\n"
            "- What to do next\n\n"
            "CRITICAL: Preserve ALL exact commands that were run, especially successful ones. "
            "These are needed to generate the final fix script.\n\n"
            "--- CONVERSATION LOG ---\n" + conversation_digest
        )

        try:
            progress_spinner("Compressing conversation history")
            summary_resp = self.client.models.generate_content(
                model=self.config.denoiser_model,
                contents=summary_prompt,
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=4096),
            )
            compressed = summary_resp.text or ""
            progress_done("compressed")
        except Exception as e:
            warning(f"LLM compaction failed ({e}), naive trim")
            first = self._main_history[0]
            self._main_history = [first] + self._main_history[-keep_last_n:]
            return

        if not compressed.strip():
            warning("Compaction empty, naive trim")
            first = self._main_history[0]
            self._main_history = [first] + self._main_history[-keep_last_n:]
            return

        # Build session memory block
        memory_block = ""
        if session_memory:
            memory_block = "\n\n=== SECTION 3: SESSION MEMORY ===\n\n" + session_memory.get_context_block()

        file_data_block = "\n\n".join(file_data_parts) if file_data_parts else "(no files read yet)"

        self._main_history = [
            types.Content(
                role="user",
                parts=[types.Part(text=(
                    "[SYSTEM: History was compacted. Below are preserved sections.]\n\n"
                    "=== SECTION 1: PRESERVED FILE CONTENTS ===\n"
                    + file_data_block
                    + "\n\n=== SECTION 2: SESSION SUMMARY ===\n\n"
                    + compressed
                    + memory_block
                ))],
            ),
            types.Content(
                role="model",
                parts=[types.Part(text=(
                    "Understood. I have file contents from Section 1, session summary "
                    "from Section 2, and session memory from Section 3. I will NOT "
                    "re-read files already listed. I will use the preserved commands "
                    "and steps for the final fix script."
                ))],
            ),
            *recent_items,
        ]

        new_estimated = self._estimate_history_chars()
        info(f"Compacted: {estimated:,} → {new_estimated:,} chars "
             f"({len(file_data_parts)} files preserved, {len(old_items)} items compressed, "
             f"{len(recent_items)} recent kept)")

    # ── Helpers ─────────────────────────────────────────────────────────

    def _default_system_prompt(self) -> str:
        """Default system prompt — used when no phase-specific prompt is provided."""
        return self.phase2_system_prompt("", "")

    def phase1_system_prompt(self, pr_link: str, project_root: str,
                              dump_root: str = "") -> str:
        """
        Phase 1 — Initial Investigation.
        Ported from PRFAgent._phase1_system_prompt().
        """
        import platform as plat
        java_home = os.environ.get("JAVA_HOME", "not set")
        android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT", "not set")
        platform_info = (
            f"OS={plat.system()} ({plat.machine()}), "
            f"Shell={os.environ.get('SHELL', 'unknown')}, "
            f"JAVA_HOME={java_home}, ANDROID_HOME={android_home}"
        )

        return f"""You are an expert Android Build Engineer performing forensic analysis.

CONTEXT:
- PR: {pr_link}
- Project Root: {project_root}
- Tasks Dump: {dump_root}
- Platform: {platform_info}

GOAL: Identify why this PR failed the Assembly stage and formulate a plan to fix
the build environment using shell commands (sed, cp, echo, mkdir, wget) or
execution configuration overrides.

FAIL FAST RULES (DISCARD IF):
- The PR requires heavy dependencies like npm, rust, cargo, ndk-build, or cmake.
- The project code itself is fundamentally broken (syntax errors, unresolved references).
- The failure is not a build issue (e.g., git merge conflict).

HOW TO READ FILES:
To read a project file, output on its own line: READ_FILE: <path>
NEVER use cat, head, tail, or any shell command to read files.

YOUR TASK (Phase 1 – Initial Investigation):
1. Read the historical logs context provided below.
2. Read the root build.gradle(.kts) using: READ_FILE: build.gradle.kts
3. Output your forensic summary with a recommended first build command.

*** CRITICAL RULES ***
- DO NOT read more than 2 files. The historical logs + root build file is enough.
- DO NOT explore subdirectories.
- When you have enough context, output your summary IMMEDIATELY.

## Expected Output: Initial Forensic Summary
- **PR Overview**: What this PR is trying to do.
- **Historical Findings**: Key errors, warnings, patterns from the logs.
- **Project Structure**: Notable build configuration details.
- **Initial Assessment**: What is going wrong and why. Fail Fast check.
- **Recommended First Step**: The single most useful command to run in Terminal A."""

    def phase2_system_prompt(self, pr_link: str, project_root: str) -> str:
        """
        Phase 2 — Interactive Build Debugging.
        Ported from PRFAgent._phase2_system_prompt().
        """
        import platform as plat
        java_home = os.environ.get("JAVA_HOME", "not set")
        android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT", "not set")
        platform_info = (
            f"OS={plat.system()} ({plat.machine()}), "
            f"Shell={os.environ.get('SHELL', 'unknown')}, "
            f"JAVA_HOME={java_home}, ANDROID_HOME={android_home}"
        )

        return f"""You are an expert Android Build Engineer. Diagnose fast, fix fast. No exploring.

CONTEXT:
- PR: {pr_link}
- Project Root: {project_root}
- Platform: {platform_info}

GOAL: Make `./gradlew assembleDebug` (or equivalent) succeed WITHOUT modifying app source code.

═══ YOUR TOOLS (use these EXACT formats) ═══

READ a file (system reads it for you — NEVER use cat/head/tail):
  READ_FILE: <relative_path>

EDIT or CREATE a file (system writes it — for build config files only):
  WRITE_FILE: <relative_path>
  ```
  <full file content>
  ```

SUGGEST a command for the user to run in Terminal A:
  ```bash
  <command>
  ```

DECLARE a verdict when done:
  VERDICT: BUILD_FIXED
  REASON: <what fixed it>

═══ FIXING STRATEGIES ═══
1. Shell Patching: sed, cp, echo, mkdir to patch build environment.
2. OOM: echo "org.gradle.jvmargs=-Xmx4g -XX:MaxMetaspaceSize=1g" >> gradle.properties
3. Missing Configs: Create mock google-services.json using WRITE_FILE:
4. Missing Dependencies: Download JAR or inject maven repos.
5. Wrong Command: Find the correct assemble task.
6. JAVA VERSION MISMATCH: If Java compatibility error, TRY MULTIPLE VERSIONS:
   - If Java 21 fails → try Java 17. If Java 17 fails → try Java 11.
   - macOS: export JAVA_HOME=$(/usr/libexec/java_home -v 17)
   - Linux: export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
   - NEVER get stuck on one Java version. Switch IMMEDIATELY if it fails.
7. Gradle Version: ./gradlew wrapper --gradle-version X.Y
8. DISCARD: NPM/Rust/NDK/CMake required → VERDICT: BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED

═══ BEHAVIOR RULES ═══
1. DO NOT EXPLORE or search. Diagnose from the error you already have.
2. DO NOT use grep. You already have the error — fix it directly.
3. Each response MUST end with a concrete action: a fix command, WRITE_FILE, or VERDICT.
4. Use READ_FILE: to read files. NEVER suggest cat/ls/grep/find to the user.
5. ONE fix per turn. Suggest it, let user rebuild, then assess next error.
6. If a fix fails, TRY A DIFFERENT APPROACH. Do not repeat the same fix.
7. If build shows "BUILD SUCCESSFUL" → VERDICT: BUILD_FIXED
8. When unfixable → VERDICT: BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED

═══ ANTI-HALLUCINATION ═══
- Only reference errors you actually saw in the logs.
- NEVER re-read a file already in your context.
- If you suggested the same fix twice, switch strategy or declare unfixable."""

    def phase3_success_prompt(self, pr_link: str, project_root: str,
                                build_steps: list[str] = None) -> str:
        """Phase 3 — Build Fix Summary (success path)."""
        import platform as plat
        steps_text = ""
        if build_steps:
            steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(build_steps))

        return f"""You are an expert Android Build Engineer.

The user has successfully built the project for this PR: {pr_link}
Project root: {project_root}
Platform: {plat.system()} ({plat.machine()})

Based on the ENTIRE debugging session, provide a concise build-fix report:

## ✅ Build Fix Summary
### PR
{pr_link}
### What Was Broken
- Root cause(s) — be specific (dependency conflicts, missing SDK, gradle misconfiguration)
- Key error messages that pointed to the issue.
### What Fixed It
- Each shell fix applied (sed, cp, echo), in order, with WHY it was needed.
### Notes
- Caveats, fragile aspects, things to watch out for.

Be concise and technical. No filler."""

    def phase3_failure_prompt(self, pr_link: str, project_root: str) -> str:
        """Phase 3 — Build Failure Conclusion."""
        return f"""You are an expert Android Build Engineer.

The user was unable to build the project for this PR: {pr_link}

Provide a failure report:
## ❌ Build Failure Conclusion
- **PR**: {pr_link}
- **Root Cause**: Clear explanation of why the build fails.
- **Discard Reason**: Choose ONE:
  - PR requires heavy dependency ❌ (npm, cargo, cmake, ndk-build)
  - PR is broken ❌ (compilation failed, syntax error upstream)
  - Not a build failure ❌ (git merge conflict, pipeline issue)
  - Time exceeded ❌ (fix requires too much rewriting)
  - Requires library mirror ⏱️ (missing 3rd party dependency)
- **Attempted Fixes**: Summary of what was tried.
- **Blockers**: Specific technical constraints.

Be precise and technical."""
