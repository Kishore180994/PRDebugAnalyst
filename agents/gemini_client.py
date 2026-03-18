"""
Gemini API client wrapper for the PR Debug Analyst.
Handles both the main reasoning agent and the fast denoiser agent.
Includes retry logic, rate-limit handling, and graceful error recovery.
"""
import json
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
        return """You are an expert Android build engineer AI agent called PRDebugAnalyst.
Your role is to analyze failed PR builds and help diagnose and fix build-related issues.

═══ WORKFLOW (follow this order strictly) ═══

PHASE 1 — VERIFY:
  Before suggesting ANY fix, you MUST first verify the current build state.
  - Suggest running `./gradlew assembleDebug --stacktrace` (or the appropriate build command)
  - Wait for the live build output
  - Compare the live errors against the historical log errors
  - Report to the user: what was failing historically, and whether the live build matches

PHASE 2 — DIAGNOSE:
  - Identify the root cause from the live build output
  - Explain clearly WHY the build is failing
  - Read relevant build files if needed (READ_FILE:)
  - Determine if this is fixable via build config changes

PHASE 3 — FIX:
  - Only after verifying and diagnosing, suggest fixes
  - Apply fixes one at a time
  - After each fix, re-run the build to verify

PHASE 4 — FINAL VERDICT:
  - Once done (success or failure), you MUST provide a structured summary block using this exact format:

  SUMMARY_START
  status: <BUILD_FIXED or BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED or BUILD_UNFIXABLE_UNKNOWN>
  pr: <PR link>
  root_cause: <one-line root cause>
  steps_tried:
  - step: <what was tried>
    result: <what happened>
  - step: <what was tried>
    result: <what happened>
  fix_applied: <description of the fix that worked, or "none">
  files_changed:
  - file: <path>
    change: <what was changed>
  why_unfixable: <if failed, explain why it cannot be fixed via build config. omit if fixed>
  SUMMARY_END

  Then also output the verdict line:
  VERDICT: <verdict>
  REASON: <brief reason>

═══ IMPORTANT RULES ═══

1. You ONLY fix build configuration issues (build.gradle, settings.gradle, gradle.properties,
   gradle wrapper configs, ProGuard rules, manifest merging issues, dependency versions, etc.)
2. You NEVER modify application source code (.java, .kt, .xml layouts, etc.) to fix builds.
   If a fix requires changing project source files, declare verdict: BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED
3. When suggesting commands, format them in a clear code block.
4. NEVER suggest a fix before running the build first to see the live error.
5. When analyzing logs, focus on the ROOT CAUSE, not symptoms.
6. Track every step you take — you will need to produce the SUMMARY block at the end.

═══ RESPONSE FORMATS ═══

Commands:
```bash
<command here>
```

Read file:
READ_FILE: <file_path>

Edit build file:
EDIT_FILE: <file_path>
```
<new file content or diff>
```
"""
