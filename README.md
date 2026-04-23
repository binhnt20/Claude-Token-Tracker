# Claude Code Token Tracker

Cross-platform desktop tool that scans local Claude Code transcripts and generates an interactive HTML dashboard showing token usage.

## Quick Start

```bash
# Run directly with Python (no install needed)
python3 app.py              # All-time usage
python3 app.py today        # Today only
python3 app.py yesterday    # Yesterday only
python3 app.py 2026-04-21   # Specific date
```

## Build Standalone Executable

```bash
pip install pyinstaller
python3 build.py
# Output: dist/claude-token-tracker
```

## CI/CD

Push a tag `v*` to trigger GitHub Actions build for macOS, Linux, and Windows. Artifacts are uploaded as release assets.

## Files

```
app.py              # Main entry point
track_tokens.py     # Data collection from ~/.claude/projects/
dashboard.py        # HTML dashboard generator
assets/chart.min.js # Chart.js (inlined in HTML)
build.py            # PyInstaller build script
token_usage.json    # Generated report (gitignored)
```
