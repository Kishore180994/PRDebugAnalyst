# PR Debug Analyst

AI-powered Android build failure debugger using Google Gemini.

Analyzes failed PR builds by searching historical logs, diagnosing errors, and guiding fixes through shell patching — all without modifying application source code.

## Setup

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="your-api-key-here"
```

## Quick Start

### Terminal UI (default)

```bash
python main.py
```

### Terminal UI + Live Web Dashboard

```bash
python main.py --web
```

Opens a two-panel browser dashboard at `http://127.0.0.1:5000`:
- **Left panel**: Live agent feed (messages, tool calls, commands, verdicts)
- **Right panel**: Terminal A output streamed via xterm.js

Custom port:
```bash
python main.py --web --port 8080
```

## How It Works

The tool walks you through:

1. **API Key** — Set `GEMINI_API_KEY` or enter interactively
2. **Tasks Folder** — Point to your historical build log dump
3. **PR Link** — The GitHub PR to debug
4. **Project Setup** — Auto-clones the repo and checks out the PR branch
5. **Mode Selection** — Manual (you run commands) or Auto (agent runs everything)

## Modes

### Manual Mode (Two-Terminal Setup)

**Terminal A** is your Android project directory. **Terminal B** runs this script.

On startup, the tool displays a `script` command to paste into Terminal A:

```
╭──────────────────────────────────────────────────────────╮
│ TERMINAL A SETUP                                         │
│                                                          │
│ In Terminal A, run the command below to start recording:  │
│                                                          │
│   script -a -F /tmp/prdebug_terminal_a.log               │
╰──────────────────────────────────────────────────────────╯
```

This records all Terminal A output automatically. No need to pipe each command through `tee`.

Once recording is active, the agent suggests commands. You run them in Terminal A, then press Enter in Terminal B to scan the output.

**Actions in Terminal B:**

| Key | Action |
|-----|--------|
| `Enter` | Scan Terminal A logs, denoise, feed to agent |
| `done` | Build succeeded — generate fix script |
| `fail` | Build unfixable — generate failure report |
| `quit` | Exit session |
| Any text | Send a message to the agent |

### Auto Mode

Fully autonomous. The agent executes commands, reads logs, edits build files, and continues until it reaches a definitive verdict. Runs until `BUILD_FIXED` or `BUILD_UNFIXABLE` (soft warning at 25 iterations, hard limit at 100).

Press `Ctrl+C` to pause and get a menu: continue, skip, or quit.

## Agent Tools

The AI agent has access to these read-only and setup tools:

| Tool | Description |
|------|-------------|
| `read_project_file(path, start_line, num_lines)` | Read specific line ranges from project files |
| `list_directory(path)` | List files/dirs with `[DIR]`/`[FILE]` prefixes |
| `grep_project(pattern, path, file_glob)` | Regex search across project files |
| `find_files(name_pattern, path)` | Find files by glob pattern |
| `search_historical_logs(pr_link)` | 3-stage forensic log extraction from the Tasks dump |
| `read_terminal_session_log(num_lines)` | Read and denoise Terminal A output |
| `run_setup_command(command)` | Execute safe build environment commands (sed, cp, echo, brew/apt install, gradlew, git, etc.) |

The `run_setup_command` tool uses an allowlist of safe command prefixes and blocks dangerous patterns.

## Fixing Strategies

The agent uses these approaches (from PRFAgent):

1. **Shell Patching** — `sed`, `cp`, `echo`, `mkdir` to patch build config on the fly
2. **Memory/OOM Fixes** — Append `org.gradle.jvmargs=-Xmx4g` to gradle.properties
3. **Missing Configs** — Copy mock `google-services.json` or create dummy files
4. **Missing Dependencies** — Download JARs or inject maven repository blocks
5. **Wrong Build Command** — Identify the correct assemble task
6. **Fail Fast** — Discard PRs requiring npm, rust, cargo, ndk-build, or cmake

## Verdicts

| Verdict | Meaning |
|---------|---------|
| `BUILD_FIXED` | Build resolved via environment/config changes |
| `BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED` | Fix requires source code changes |
| `BUILD_UNFIXABLE_UNKNOWN` | Could not determine a fix |

## Project Structure

```
PRDebugAnalyst/
├── main.py                    # Entry point (--web flag for dashboard)
├── config.py                  # Configuration
├── requirements.txt
├── agents/
│   ├── gemini_client.py       # Gemini API wrapper + Phase 1/2/3 prompts
│   └── action_parser.py       # Parses agent responses into actions
├── modes/
│   ├── manual_mode.py         # Interactive manual mode
│   └── auto_mode.py           # Autonomous auto mode
├── utils/
│   ├── display.py             # CLI display (PRFAgent style)
│   ├── terminal_bridge.py     # Terminal bridge + agent tools
│   ├── log_analyzer.py        # Historical log analysis
│   ├── session_memory.py      # Persistent session memory (survives history trimming)
│   ├── session_report.py      # Reports, fix scripts, JSON export
│   ├── git_ops.py             # Auto clone/checkout from PR link
│   └── web_events.py          # Event emitter for web dashboard
├── web/
│   ├── server.py              # Flask-SocketIO server
│   └── templates/
│       └── dashboard.html     # Two-panel live dashboard
└── dashboard.html             # Static design reference
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | (required) | Google Gemini API key |
| `MAIN_MODEL` | `gemini-3-flash-preview` | Main reasoning agent model |
| `DENOISER_MODEL` | `gemini-2.5-flash` | Fast log denoiser model |
