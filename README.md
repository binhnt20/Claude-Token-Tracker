# Claude Code Token Tracker

Cross-platform desktop tool that scans local Claude Code transcripts (`~/.claude/projects/`) and generates an interactive HTML dashboard showing token usage. Runs in a native window (pywebview) with a browser fallback.

Docs: [project overview & PDR](docs/project-overview-pdr.md) | [system architecture](docs/system-architecture.md) | [codebase summary](docs/codebase-summary.md) | [code standards](docs/code-standards.md) | [roadmap](docs/project-roadmap.md)

## Quick Start

```bash
pip install pywebview   # optional but recommended; without it, app.py falls back to your browser
python3 app.py           # scans all-time data, opens the dashboard
```

`app.py` always scans **all-time** data — no CLI date args (only `-h`/`--help` is recognized; other args are ignored). Date filtering happens **inside the dashboard UI** (Today / Yesterday / Last 7 / Last 30 / All time / Custom range buttons).

To generate a date-filtered `token_usage.json` report from the CLI instead, use `track_tokens.py` directly (no dashboard, prints a text summary):

```bash
python3 track_tokens.py               # all-time
python3 track_tokens.py today         # UTC "today" only
python3 track_tokens.py yesterday     # UTC "yesterday" only
python3 track_tokens.py 2026-04-21    # specific date (YYYY-MM-DD)
```

No dependency manifest is checked in — install `pywebview` (and `pyinstaller` for builds) manually; see [code-standards.md](docs/code-standards.md) for why.

## Build Standalone Executable

```bash
pip install pyinstaller pywebview
python3 build.py
# macOS:   dist/claude-token-tracker.app (+ .dmg if create-dmg is installed)
# Linux:   dist/claude-token-tracker (+ .AppImage if appimagetool is installed)
# Windows: dist/claude-token-tracker.exe
```

Full local build + CI/CD walkthrough (Vietnamese): [DEPLOY.md](DEPLOY.md).

### Add to the Linux application menu

The `.AppImage` is a single file, so it won't show up in your app menu by default. Register it once — this extracts the app into `~/.local/lib/` and adds a menu entry, so it launches from the menu **without needing FUSE**:

```bash
chmod +x claude-token-tracker-x86_64.AppImage
./claude-token-tracker-x86_64.AppImage --appimage-extract-and-run --install     # extract + add menu entry
./claude-token-tracker-x86_64.AppImage --appimage-extract-and-run --uninstall   # remove app + menu entry
```

Then search "Claude Token Tracker" in your application menu and launch it like any other app. The `--appimage-extract-and-run` prefix lets the installer run without FUSE on Ubuntu 24.04.

## CI/CD

Push a tag `v*` (or run the workflow manually) to trigger GitHub Actions builds for macOS, Linux, and Windows. On a tag push, `.dmg`/`.AppImage`/`.exe` artifacts are attached to a GitHub Release automatically.

## Files

```
app.py              # Desktop entry point (always all-time scan + native window)
track_tokens.py     # Data collection/aggregation + its own date-filtered CLI
pricing.py          # Rate tables + cost math (the only file with price numbers)
test_pricing.py     # Tests for pricing.py (stdlib unittest)
dashboard.py        # Self-contained HTML dashboard generator (Chart.js + flatpickr inlined)
build.py            # PyInstaller build script (macOS/Linux/Windows)
gen_icon.py         # App icon generator (macOS-only, uses AppKit)
assets/             # Chart.js, flatpickr, icons — inlined/bundled into the app
token_usage.json    # Generated report (gitignored)
```

Cost is **API-equivalent** — what usage would cost at published list prices, not a subscription bill. Unrecognized models are reported as unpriced rather than guessed at another model's rate. Run pricing tests with `python3 -m unittest test_pricing`.

No `LICENSE` file is present in this repository.
