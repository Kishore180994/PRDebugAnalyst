"""
Parses the AI agent's responses to extract structured actions.
The agent may respond with commands to run, files to read/edit, or verdicts.
"""
import re
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class ActionType(Enum):
    COMMAND = "command"          # A bash command to execute
    READ_FILE = "read_file"     # Request to read a project file
    EDIT_FILE = "edit_file"     # Request to edit a build file
    VERDICT = "verdict"         # Final verdict on the PR
    MESSAGE = "message"         # Informational message (no action needed)


@dataclass
class AgentAction:
    """A parsed action from the agent's response."""
    action_type: ActionType
    content: str                    # The command, file path, or message
    metadata: dict = None           # Extra data (e.g., file content for edits, verdict reason)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ActionParser:
    """Parses structured actions from the AI agent's text responses."""

    # Patterns to detect in agent responses
    COMMAND_PATTERN = re.compile(r"```(?:bash|shell|sh)?\s*\n(.*?)\n```", re.DOTALL)
    READ_FILE_PATTERN = re.compile(r"READ_FILE:\s*(.+?)(?:\n|$)")
    EDIT_FILE_PATTERN = re.compile(r"EDIT_FILE:\s*(.+?)\s*\n```\s*\n?(.*?)\n```", re.DOTALL)
    VERDICT_PATTERN = re.compile(r"VERDICT:\s*(\S+)")
    REASON_PATTERN = re.compile(r"REASON:\s*(.+?)(?:\n\n|\n(?:VERDICT|READ_FILE|EDIT_FILE|```)|$)", re.DOTALL)

    @classmethod
    def parse(cls, response: str) -> list[AgentAction]:
        """
        Parse the agent's response into a list of actions.
        A single response may contain multiple actions.
        """
        actions = []

        # Check for verdict first (highest priority)
        verdict_match = cls.VERDICT_PATTERN.search(response)
        if verdict_match:
            reason_match = cls.REASON_PATTERN.search(response)
            reason = reason_match.group(1).strip() if reason_match else "No reason provided"
            actions.append(AgentAction(
                action_type=ActionType.VERDICT,
                content=verdict_match.group(1).strip(),
                metadata={"reason": reason},
            ))
            return actions  # Verdict is terminal, no other actions matter

        # Check for file read requests
        for match in cls.READ_FILE_PATTERN.finditer(response):
            filepath = match.group(1).strip()
            actions.append(AgentAction(
                action_type=ActionType.READ_FILE,
                content=filepath,
            ))

        # Check for file edit requests
        for match in cls.EDIT_FILE_PATTERN.finditer(response):
            filepath = match.group(1).strip()
            content = match.group(2).strip()
            actions.append(AgentAction(
                action_type=ActionType.EDIT_FILE,
                content=filepath,
                metadata={"new_content": content},
            ))

        # Check for commands (bash code blocks)
        for match in cls.COMMAND_PATTERN.finditer(response):
            command = match.group(1).strip()
            # Don't add if it's actually file content for an edit
            if not any(a.action_type == ActionType.EDIT_FILE and a.metadata.get("new_content", "").startswith(command[:50]) for a in actions):
                actions.append(AgentAction(
                    action_type=ActionType.COMMAND,
                    content=command,
                ))

        # If no structured actions found, treat as informational message
        if not actions:
            actions.append(AgentAction(
                action_type=ActionType.MESSAGE,
                content=response,
            ))

        return actions

    @classmethod
    def has_verdict(cls, response: str) -> bool:
        """Quick check if the response contains a verdict."""
        return bool(cls.VERDICT_PATTERN.search(response))

    @classmethod
    def extract_verdict(cls, response: str) -> Optional[tuple[str, str]]:
        """Extract verdict and reason if present. Returns (verdict, reason) or None."""
        verdict_match = cls.VERDICT_PATTERN.search(response)
        if not verdict_match:
            return None
        reason_match = cls.REASON_PATTERN.search(response)
        reason = reason_match.group(1).strip() if reason_match else "No reason provided"
        return verdict_match.group(1).strip(), reason
