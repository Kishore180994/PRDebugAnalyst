# PR Debug Analyst

AI-powered Android build failure debugger using Google Gemini.

## Setup

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="your-api-key-here"
```

## Usage

```bash
python main.py
```

The tool will walk you through:
1. Pointing to your **Tasks folder** (historical build logs)
2. Entering the **PR link** to debug
3. Entering the **Android project path**
4. Choosing a mode: **Manual** or **Auto**

## Modes

### Manual Mode (Two-Terminal Setup)
- **Terminal A**: Your Android project directory (where you run build commands)
- **Terminal B**: This script (`python main.py`)

Actions in manual mode:
- **Enter** — Scan newest logs from Terminal A, denoise, and feed to the agent
- **done** — Report the last step succeeded
- **fail** — Report the last step failed
- **quit** — Exit
- **Any text** — Send a message directly to the AI agent

Pipe Terminal A output to a log file for the agent to read:
```bash
./gradlew assembleDebug 2>&1 | tee -a /tmp/prdebug_terminal_a.log
```

### Auto Mode
Fully autonomous. The agent executes commands, reads logs, edits build files, and continues until it reaches a verdict.

## Verdicts

| Verdict | Meaning |
|---------|---------|
| `BUILD_FIXED` | Build issue resolved by editing build config files |
| `BUILD_UNFIXABLE_PROJECT_CHANGES_REQUIRED` | Fix requires source code changes — PR discarded |
| `BUILD_UNFIXABLE_UNKNOWN` | Could not determine a fix |
| `NEEDS_MORE_INVESTIGATION` | More analysis needed |

## Project Structure

```
PRDebugAnalyst/
├── main.py                  # Entry point
├── config.py                # Configuration
├── requirements.txt
├── agents/
│   ├── gemini_client.py     # Gemini API wrapper (main + denoiser)
│   └── action_parser.py     # Parses agent responses into actions
├── modes/
│   ├── manual_mode.py       # Interactive manual mode
│   └── auto_mode.py         # Autonomous auto mode
└── utils/
    ├── log_analyzer.py      # Log parsing and historical analysis
    ├── terminal_bridge.py   # Terminal A/B communication bridge
    └── display.py           # CLI display utilities
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | (required) | Google Gemini API key |
| `MAIN_MODEL` | `gemini-3-flash-preview` | Main reasoning agent model |
| `DENOISER_MODEL` | `gemini-2.5-flash` | Fast log denoiser model |
