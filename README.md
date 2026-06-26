# Claude Widget

A Windows desktop widget that monitors your [Claude Code](https://claude.ai/code) usage in real time — no API key required.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![PyQt6](https://img.shields.io/badge/UI-PyQt6-green) ![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)

## What it shows

| Section | Info |
|---|---|
| **Model** | Active model name and maximum context window |
| **Sessions** | Live status of every running Claude Code process |
| **Active Session** | Tokens used in the current conversation, with a progress bar |
| **Today** | Input / output tokens, cache reads (with savings), estimated cost |
| **This Month** | Total tokens and estimated cost |

The cost figures are labelled `Cost~` — they represent the API-equivalent price, useful as a reference even if you are on a claude.ai subscription.

### Session status indicators

| Symbol | Colour | Meaning |
|---|---|---|
| `●` | Green | busy — actively processing |
| `◑` | Yellow | waiting · input — idle, waiting for your next message |
| `◉` | Orange | waiting · permission — a tool use needs your approval |
| `○` | Gray | closed — process has exited |

Up to 4 sessions are shown simultaneously. The widget checks whether each process PID is still alive via the Win32 API (no extra dependencies).

## How it works

Claude Code stores every conversation as a JSONL file under `~/.claude/projects/`. Each assistant message includes a `usage` object with exact token counts. The widget reads those files directly, deduplicates messages by `message.id` (Claude Code writes each message 2–3 times during streaming), and aggregates the results.

A `QFileSystemWatcher` detects when Claude Code writes new data and triggers a refresh within 2 seconds — no polling, no API calls.

## Requirements

- Windows 10 / 11
- Python 3.10+
- [Claude Code](https://claude.ai/code) installed and used at least once

## Installation

```bash
git clone https://github.com/lvirzi/claudewidget.git
cd claudewidget
pip install PyQt6
python claude_widget.py
```

## Build a standalone .exe

```bash
pip install pyinstaller
build.bat
# Output: dist\ClaudeWidget.exe  (~40 MB, no Python needed)
```

## Auto-start with Windows

Place a shortcut to `ClaudeWidget.exe` in:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

## Usage

| Action | Result |
|---|---|
| Click tray icon | Show / hide the panel |
| Right-click tray icon | Menu: Refresh, Settings, Quit |
| Drag the panel | Reposition (saved automatically) |
| 📌 button | Toggle always-on-top — white background = pinned (on top), transparent = free |
| ⚙ button | Open settings (refresh interval) |
| ↻ button | Force immediate refresh |

## Project structure

```
claude_widget.py   # Full source — single file, ~400 lines
requirements.txt   # PyQt6 only, no API key needed
build.bat          # PyInstaller build script
```

## License

MIT
