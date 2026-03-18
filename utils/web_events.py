"""
Web Event Emitter for PR Debug Analyst.

Provides a global event system that display.py functions use to push
structured events to the web dashboard. The Flask-SocketIO server
consumes these events and broadcasts them to connected browsers.

This is a bridge between the terminal UI and the web UI — both can
run simultaneously. Terminal output continues to work normally; web
events are emitted in addition to terminal output.
"""
import queue
import threading
import json
from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from enum import Enum


class EventType(str, Enum):
    """All event types the web dashboard can receive."""
    # Agent communication
    AGENT_MESSAGE = "agent_message"
    THINKING_START = "thinking_start"
    THINKING_END = "thinking_end"

    # Tool use
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"

    # Commands
    COMMAND = "command"
    COMMANDS = "commands"

    # Status messages
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

    # Progress
    SPINNER_START = "spinner_start"
    SPINNER_DONE = "spinner_done"
    SPINNER_CANCEL = "spinner_cancel"

    # Session flow
    SECTION = "section"
    STEP = "step"
    VERDICT = "verdict"
    SCRIPT_SETUP = "script_setup"

    # File operations
    FILE_EDIT_PREVIEW = "file_edit_preview"
    FILE_EDIT_RESULT = "file_edit_result"

    # Session lifecycle
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    STATS = "stats"

    # Reports
    REPORT_SUCCESS = "report_success"
    REPORT_FAILURE = "report_failure"
    SCRIPT_GENERATED = "script_generated"

    # Log table
    LOG_SUMMARY = "log_summary"

    # Manual mode help
    CONTROLS = "controls"


@dataclass
class WebEvent:
    """A single event to be sent to the web dashboard."""
    event_type: str
    data: dict = field(default_factory=dict)


class WebEventEmitter:
    """
    Thread-safe event emitter. Display functions call emit() to push
    events. The Flask-SocketIO server calls get_events() to consume them.
    """

    def __init__(self):
        self._queue: queue.Queue[WebEvent] = queue.Queue()
        self._enabled = False
        self._callback = None  # Direct callback for SocketIO emit

    def enable(self):
        """Enable web event emission."""
        self._enabled = True

    def disable(self):
        """Disable web event emission."""
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def set_callback(self, callback):
        """
        Set a direct callback function for immediate event delivery.
        callback(event_type: str, data: dict)
        Used by the SocketIO server for real-time push.
        """
        self._callback = callback

    def emit(self, event_type: EventType, **data):
        """
        Emit an event to the web dashboard.
        No-op if web events are disabled.
        """
        if not self._enabled:
            return

        event = WebEvent(event_type=event_type.value, data=data)

        # Direct callback for real-time delivery
        if self._callback:
            try:
                self._callback(event.event_type, event.data)
            except Exception:
                pass  # Never let web events break the main flow

        # Also queue for polling-based consumers
        self._queue.put(event)

    def get_events(self, max_events: int = 50) -> list[dict]:
        """
        Consume queued events (non-blocking).
        Returns list of {event_type, data} dicts.
        """
        events = []
        while not self._queue.empty() and len(events) < max_events:
            try:
                event = self._queue.get_nowait()
                events.append({"event_type": event.event_type, "data": event.data})
            except queue.Empty:
                break
        return events

    def clear(self):
        """Discard all queued events."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


# ═══════════════════════════════════════════════════════════════════════════
#  Global singleton — imported by display.py and server.py
# ═══════════════════════════════════════════════════════════════════════════

emitter = WebEventEmitter()
