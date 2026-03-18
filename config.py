"""
Configuration for PR Debug Analyst Agent.
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    """Central configuration for the agent."""

    # Gemini models
    main_model: str = "gemini-3-flash-preview"
    denoiser_model: str = "gemini-2.5-flash"

    # API
    gemini_api_key: str = ""

    # Paths (set at runtime)
    tasks_folder: str = ""          # Where historical build logs live
    project_folder: str = ""        # Where the Android project is (Terminal A)
    terminal_a_log_file: str = ""   # Log file being written by Terminal A

    # Agent settings
    max_context_tokens: int = 80000
    max_retries: int = 3
    log_poll_interval: float = 2.0  # seconds between log polls in auto mode

    # Denoiser settings
    denoise_max_lines: int = 500    # max lines to send to denoiser at once

    # Verdict
    verdict_options: list = field(default_factory=lambda: [
        "BUILD_FIXED",
        "BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED",
        "BUILD_UNFIXABLE_UNKNOWN",
        "NEEDS_MORE_INVESTIGATION",
    ])

    def validate(self) -> list[str]:
        """Return list of validation errors, empty if valid."""
        errors = []
        if not self.gemini_api_key:
            errors.append("GEMINI_API_KEY is required. Set it as an environment variable or pass it in.")
        if not self.tasks_folder or not os.path.isdir(self.tasks_folder):
            errors.append(f"Tasks folder not found: {self.tasks_folder}")
        return errors

    @classmethod
    def from_env(cls) -> "Config":
        """
        Create config from environment variables with sensible defaults.
        Priority: environment variable > hardcoded default in this class.

        SECURITY: Always use the GEMINI_API_KEY environment variable.
        Never hardcode API keys in source files to avoid accidental
        exposure in version control.
        """
        instance = cls()
        # Only override from env if the env var is actually set
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            instance.gemini_api_key = env_key
        # Same for model overrides
        env_main = os.environ.get("MAIN_MODEL")
        if env_main:
            instance.main_model = env_main
        env_denoiser = os.environ.get("DENOISER_MODEL")
        if env_denoiser:
            instance.denoiser_model = env_denoiser
        return instance
