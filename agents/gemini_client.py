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
        Returns cleaned, relevant-only log content.
        Falls back to raw logs if all retries fail.
        """
        prompt = f"""You are a build log denoiser for Android projects.
Your job is to take raw build/terminal output and extract ONLY the relevant information.

KEEP:
- Error messages and stack traces
- Warning messages related to the build failure
- Gradle task names that failed
- File paths mentioned in errors
- Dependency resolution errors
- Configuration errors
- Any "BUILD FAILED" or similar status lines
- Compiler errors with line numbers

REMOVE:
- Download progress bars and percentages
- Successful task completions (unless they provide context for a failure)
- Verbose debug logging that isn't related to errors
- Repeated identical log lines (keep just one instance)
- ASCII art, banners, and decorative output
- Timestamp prefixes (keep the message content)
- Gradle daemon startup messages
- Memory/GC statistics (unless related to OOM errors)

Output the denoised log content directly, no commentary. If the logs contain no errors,
output "NO_ERRORS_FOUND" followed by a brief summary of what the logs show.

RAW LOGS:
```
{raw_logs}
```"""

        def _do_call():
            response = self.client.models.generate_content(
                model=self.config.denoiser_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=4096,
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

    def trim_history(self, keep_last_n: int = 10):
        """
        Trim history to the last N turns to manage context window.
        Always keeps the first message (initial context) and last N messages.
        """
        if len(self._main_history) <= keep_last_n + 1:
            return
        first = self._main_history[0]
        recent = self._main_history[-keep_last_n:]
        self._main_history = [first] + recent

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

YOUR TASK (Phase 1 – Initial Investigation):
1. Call `search_historical_logs` to find build logs related to this PR.
2. Call `list_directory` on the project root.
3. Read key build files: build.gradle(.kts), settings.gradle(.kts).
4. Call `read_terminal_session_log` to check terminal context.

*** CRITICAL RULES ***
- SPEED IS PARAMOUNT: Read at most 1-2 files. DO NOT explore deep directories.
- NEVER read the same file twice. If you already read it, use your context.
- When you have enough context, STOP calling tools and output your summary.

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

        return f"""You are an expert Android Build Engineer guiding a user through fixing a build.

CONTEXT:
- PR: {pr_link}
- Project Root: {project_root}
- Platform: {platform_info}

GOAL: Make `./gradlew assembleDebug` (or equivalent) succeed WITHOUT modifying
application source code.

FIXING STRATEGIES (PREFERRED APPROACH):
1. Shell Patching: Use sed, cp, echo, mkdir, wget to patch the build environment.
2. Memory Issues (OOM/Timeout): echo "org.gradle.jvmargs=-Xmx4g" >> gradle.properties
3. Missing Configs: cp mock google-services.json or create a dummy one with echo.
4. Missing Dependencies: Download JAR/POM manually or inject maven repositories.
5. Wrong Command: Identify the correct assemble task.
6. DISCARD: If build requires NPM, Rust, NDK, CMake → "BUILD UNFIXABLE — type 'fail'".

PROTOCOL:
1. Review the SYSTEM STATE. DO NOT re-read files/directories already cached.
2. Read the latest terminal logs using `read_terminal_session_log`.
3. Focus ONLY on actual errors from the denoised log output.
4. If build shows "BUILD SUCCESSFUL", output "BUILD SUCCEEDED — type 'done'."
5. When formulating instructions, put commands in a SINGLE fenced code block.

*** ANTI-HALLUCINATION & CACHE RULES ***
- Trust the Denoising Agent. If a detail is not in the denoised log, ignore it.
- NEVER guess file paths. Believe the user if they say a file doesn't exist.
- NEVER call read_project_file or list_directory on paths already in your history.
- SPEED IS PARAMOUNT: Read terminal logs ONCE per turn.
- Use grep_project and find_files to locate things instead of manual exploration.
- DO NOT re-read files you've already read. Use the information from previous reads.
- NEVER declare BUILD_FIXED without actually seeing BUILD SUCCESSFUL in the logs.
- Make FORWARD PROGRESS on each turn — don't loop on the same analysis."""

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
