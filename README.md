# Claude Code Token Tracker

Cross-platform desktop tool that scans local Claude Code transcripts and generates an interactive HTML dashboard showing token usage. Runs in a native window (pywebview) with a browser fallback.

Transcripts are read from `~/.claude/projects/` and `~/.config/claude/projects/`, or from `<dir>/projects/` for each directory in `CLAUDE_CONFIG_DIR` (comma-separated) when that variable is set — the same places Claude Code writes to.

## Quick Start

```bash
pip install pywebview   # optional but recommended; without it, app.py falls back to your browser
python3 app.py           # scans all-time data, opens the dashboard
```

`app.py` always scans **all-time** data — no CLI date args (only `-h`/`--help` is recognized; other args are ignored). Date filtering happens **inside the dashboard UI** (Today / Yesterday / Last 7 / Last 30 / All time / Custom range buttons).

To generate a date-filtered `token_usage.json` report from the CLI instead, use `track_tokens.py` directly (no dashboard, prints a text summary):

```bash
python3 track_tokens.py               # all-time
python3 track_tokens.py today         # your local "today" only
python3 track_tokens.py yesterday     # your local "yesterday" only
python3 track_tokens.py 2026-04-21    # specific date (YYYY-MM-DD)
```

No dependency manifest is checked in — the tracker itself needs only the Python standard library; install `pywebview` (and `pyinstaller` for builds) manually.

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
sources.py          # Finds every transcript directory and file
pricing.py          # Rate tables + cost math (the only file with price numbers)
dashboard.py        # Self-contained HTML dashboard generator (Chart.js + flatpickr inlined)
test_pricing.py     # Tests for pricing.py (stdlib unittest)
test_track_tokens.py  # Tests for parsing, de-duplication, sources, dates and projects
test_dashboard.py   # Tests for the dashboard page (date helpers run under node when installed)
test_app.py         # Tests for the desktop window (pywebview stubbed)
build.py            # PyInstaller build script (macOS/Linux/Windows)
gen_icon.py         # App icon generator (macOS-only, uses AppKit)
assets/             # Chart.js, flatpickr, icons — inlined/bundled into the app
token_usage.json    # Generated report (gitignored)
```

## How numbers are counted

- **Each API call counts once.** Claude Code writes one transcript line per content block of a response, each repeating the call's usage, and a resumed session copies earlier lines into a new file. Lines are grouped by message id + request id across all files, keeping the line with the final output count. Lines Claude Code makes up itself (model `<synthetic>`, no API call behind them) are not counted.
- **Cost is API-equivalent** — what usage would cost at published list prices, not a subscription bill. Each call is priced at the rate in effect on its UTC date (`pricing.py` keeps dated rows and their sources), including cache writes (5 min / 1 h), cache reads, Haiku 5.5 prompt-length tiers, fast mode (2× on Opus 5.5, Opus 5 and Opus 4.8), US-only inference (1.1× on Claude 4.6 and later) and web search ($10 per 1,000 searches).
- **Nothing is guessed.** An unknown model, fast mode on a model without a published multiplier, or an unknown cache tier is reported as unpriced: its tokens are counted, its cost is not.
- **Days are your local days** (the computer's time zone), in the CLI, the report and the dashboard. Projects are named after the working directory recorded in their transcripts, and the project table follows the date filter.

### Price overrides

A model released after your copy of the tracker, or a contracted rate, can be priced without a new release. Create `~/.config/claude-token-tracker/pricing.json` (or point `CTT_PRICING` at another file); the shape mirrors Claude Code's `modelPricing` setting:

```json
{
  "multiplier": 0.85,
  "overrides": {
    "claude-new-model": {"input": 3, "output": 15, "cacheRead": 0.3}
  }
}
```

Rates are USD per million tokens. `cacheWrite5m` / `cacheWrite1h` default to 1.25× / 2× `input`. `multiplier` (above 0, at most 10) scales every token cost. A row for an existing model replaces the built-in rates on every date. A malformed file is ignored with one warning.

Run the tests with `python3 -m unittest`.

No `LICENSE` file is present in this repository.
