"""
Web Dashboard Server for PR Debug Analyst.

Runs a Flask-SocketIO server that:
  1. Serves the two-panel dashboard HTML
  2. Pushes agent events (left panel) via WebSocket
  3. Watches the script log file and streams terminal output (right panel)
  4. Handles user input from the web UI (optional)

Usage:
  This server is started automatically when main.py is run with --web flag.
  It runs in a background thread so the main agent loop continues normally.
"""
import os
import sys
import time
import json
import threading
from pathlib import Path

from flask import Flask, render_template_string, send_from_directory
from flask_socketio import SocketIO, emit

# Add parent to path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.web_events import emitter, EventType

# ═══════════════════════════════════════════════════════════════════════════
#  Flask App Setup
# ═══════════════════════════════════════════════════════════════════════════

app = Flask(__name__,
    template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
    static_folder=os.path.join(os.path.dirname(__file__), 'static'),
)
app.config['SECRET_KEY'] = 'prdebug-dashboard-secret'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ═══════════════════════════════════════════════════════════════════════════
#  State
# ═══════════════════════════════════════════════════════════════════════════

_log_file: str = ""
_log_position: int = 0
_watching: bool = False
_session_info: dict = {}


# ═══════════════════════════════════════════════════════════════════════════
#  Routes
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    """Serve the main dashboard page."""
    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'dashboard.html')
    with open(template_path, 'r') as f:
        template = f.read()
    return render_template_string(template)


# ═══════════════════════════════════════════════════════════════════════════
#  WebSocket Events
# ═══════════════════════════════════════════════════════════════════════════

@socketio.on('connect')
def handle_connect():
    """Send current session state to newly connected clients."""
    emit('session_info', _session_info)
    # Send any queued events
    events = emitter.get_events()
    for event in events:
        emit('agent_event', event)


@socketio.on('disconnect')
def handle_disconnect():
    pass


@socketio.on('user_input')
def handle_user_input(data):
    """
    Receive user input from the web dashboard.
    This is for future use — currently input comes from the terminal.
    """
    pass


# ═══════════════════════════════════════════════════════════════════════════
#  Agent Event Bridge
# ═══════════════════════════════════════════════════════════════════════════

def _on_agent_event(event_type: str, data: dict):
    """
    Callback registered with the web event emitter.
    Pushes agent events to all connected web clients immediately.
    """
    socketio.emit('agent_event', {
        'event_type': event_type,
        'data': data,
    })


# ═══════════════════════════════════════════════════════════════════════════
#  Terminal Log File Watcher
# ═══════════════════════════════════════════════════════════════════════════

def _watch_log_file():
    """
    Background thread that watches the script log file and pushes
    new content to the web dashboard's terminal panel via WebSocket.
    Reads raw bytes so xterm.js can render ANSI escape codes.
    """
    global _log_position, _watching
    _watching = True

    while _watching:
        if not _log_file or not os.path.exists(_log_file):
            time.sleep(0.5)
            continue

        try:
            file_size = os.path.getsize(_log_file)
            if file_size > _log_position:
                with open(_log_file, 'r', errors='replace') as f:
                    f.seek(_log_position)
                    new_content = f.read()
                    _log_position = f.tell()

                if new_content:
                    socketio.emit('terminal_output', {
                        'content': new_content,
                    })
        except (PermissionError, OSError):
            pass

        time.sleep(0.3)  # Poll every 300ms


# ═══════════════════════════════════════════════════════════════════════════
#  Server Lifecycle
# ═══════════════════════════════════════════════════════════════════════════

def start_server(port: int = 5000, log_file: str = "", pr_link: str = "", mode: str = "manual"):
    """
    Start the web dashboard server in a background thread.
    Returns the URL where the dashboard is accessible.

    Args:
        port: Port to serve on (default 5000)
        log_file: Path to the script log file for terminal streaming
        pr_link: PR link being debugged
        mode: "manual" or "auto"
    """
    global _log_file, _log_position, _session_info

    _log_file = log_file
    _log_position = 0
    _session_info = {
        'pr_link': pr_link,
        'mode': mode,
        'log_file': log_file,
    }

    # Register the event callback
    emitter.enable()
    emitter.set_callback(_on_agent_event)

    # Start log file watcher thread
    watcher_thread = threading.Thread(target=_watch_log_file, daemon=True)
    watcher_thread.start()

    # Start Flask-SocketIO in a background thread
    server_thread = threading.Thread(
        target=lambda: socketio.run(app, host='127.0.0.1', port=port, debug=False, use_reloader=False, log_output=False),
        daemon=True,
    )
    server_thread.start()

    url = f"http://127.0.0.1:{port}"
    return url


def stop_server():
    """Stop the log file watcher. Flask server will die with the process."""
    global _watching
    _watching = False
    emitter.disable()


# ═══════════════════════════════════════════════════════════════════════════
#  Standalone mode (for testing)
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("Starting PR Debug Analyst Dashboard...")
    print("Open http://127.0.0.1:5000 in your browser")
    emitter.enable()
    emitter.set_callback(_on_agent_event)
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)
