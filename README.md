# Claude Code Token Tracker

Cross-platform desktop tool that scans local Claude Code transcripts and generates an interactive HTML dashboard showing token usage. Runs in a native window (pywebview) with a browser fallback.

Transcripts are read from `~/.claude/projects/` and `~/.config/claude/projects/`, the same places Claude Code writes to, and first from `<dir>/projects/` for each directory in `CLAUDE_CONFIG_DIR` (comma-separated) when that variable is set. The variable adds folders and hides none: account switchers such as Orca set it in every shell they open, so a run there still counts the rest of the history.

**Windows + WSL on one computer.** Each side keeps its own history, and the tracker reads both: in WSL it adds the Windows user folder (`/mnt/c/Users/<you>`, found from the Windows `PATH` that WSL appends), on Windows it adds the home folders of every running WSL distribution (`\\wsl.localhost\<distro>\home\<user>`). A stopped distribution is not started and not counted. The two sides never share an API call, so the sum counts nothing twice. The dashboard footer lists every folder read and its calls. Reading the other side goes through a network share and is slow: on the measuring machine 1.1 GB of WSL history took 2 minutes from Windows, 3.4 GB of Windows history 2.5 minutes from WSL. To read one side only, set `CLAUDE_TOKEN_TRACKER_DIRS` (for example `CLAUDE_TOKEN_TRACKER_DIRS=~/.claude`, comma-separated), which replaces every default and `CLAUDE_CONFIG_DIR`.

## Quick Start

The tracker needs Python 3 (tested with 3.12). `pywebview` is optional: without it, `app.py` opens the dashboard in your browser.

```bash
# macOS (a venv also works with Homebrew's Python, which refuses a global pip)
python3 -m venv ~/.venvs/ctt
~/.venvs/ctt/bin/pip install pywebview
~/.venvs/ctt/bin/python app.py

# Windows (the python.org installer gives `python`; `python3` opens the Microsoft Store)
python -m pip install pywebview
python app.py

# Linux: the window uses Qt, and Ubuntu 23.04+ / Debian 12+ refuse a system-wide pip (PEP 668), so use a venv
sudo apt install python3-venv libxcb-cursor0
python3 -m venv ~/.venvs/ctt
~/.venvs/ctt/bin/pip install pywebview qtpy PySide6
~/.venvs/ctt/bin/python app.py
```

If Qt names another missing system library, the full `apt` list is in [DEPLOY.md](DEPLOY.md) (Linux section).

The window opens at once on a "Scanning Claude Code transcripts…" page and shows the dashboard when the scan ends (a few seconds per GB of transcripts).

`app.py` always scans **all-time** data — no CLI date args (only `-h`/`--help`, and `--install`/`--uninstall` in the Linux AppImage, are recognized; other args are ignored). Date filtering happens **inside the dashboard UI**: Today / Yesterday / Last 7 days / Last 30 days (both end today, and the chart keeps days without usage) / All time (every day in the data) / Custom range.

When no native window can open, the dashboard opens in your default browser instead and the terminal says why — for example on Linux X11 without `libxcb-cursor0`, where it prints the `apt` command. The browser page is written to a private temporary folder (`claude-token-tracker-*`, with a random file name); folders older than a day are removed at the next launch.

To generate a date-filtered `token_usage.json` report from the CLI instead, use `track_tokens.py` directly (no dashboard, prints a text summary):

```bash
python3 track_tokens.py               # all-time (on Windows: python track_tokens.py)
python3 track_tokens.py today         # your local "today" only
python3 track_tokens.py yesterday     # your local "yesterday" only
python3 track_tokens.py 2026-04-21    # specific date (YYYY-MM-DD)
python3 track_tokens.py -h            # usage
```

A date that does not exist (`2026-13-45`) exits with code 2 before scanning. "RECENT DATES" lists the 7 latest days up to today; days after today (a wrong clock) are counted in the totals and named on their own line.

`token_usage.json` is written next to `track_tokens.py` when run from source. The packaged app writes it to the user's data folder instead: `~/Library/Application Support/claude-token-tracker/` (macOS), `%LOCALAPPDATA%\claude-token-tracker\` (Windows), `~/.local/share/claude-token-tracker/` or `$XDG_DATA_HOME/claude-token-tracker/` (Linux). A file that cannot be written is a warning; the summary still prints.

No dependency manifest is checked in — the tracker itself needs only the Python standard library; install `pywebview` (plus `qtpy` and `PySide6` on Linux, and `pyinstaller` for builds) manually.

## Claude Code's share of the 5-hour and weekly limits

The plan's 5-hour and weekly limits are one pool for Claude Code, claude.ai chat, Cowork and every device, and transcripts hold tokens, not percentages. Claude Code hands the official percentages to its status line command after each response (Pro and Max accounts only). `statusline.py` records them, and the dashboard and the CLI show the part that Claude Code on this computer used:

> Claude Code used 30.2% of the 5-hour limit (account total 38.0%) · resets 14:05 · as of 13:52

Turn it on once per side, from a source checkout (Windows and WSL each have their own Claude Code settings):

```bash
python3 statusline.py --install     # on Windows: python statusline.py --install
python3 statusline.py --uninstall   # put the previous status line back
```

`--install` edits `~/.claude/settings.json` (or the one in `CLAUDE_CONFIG_DIR`) after saving a backup next to it. A status line you already have (yours, or Orca's) keeps working: the new command passes Claude Code's input to both and shows your status line's output; with none, it shows `5h 38% · 7d 41%`. A second install changes nothing. Samples go to `limits.jsonl` in the tracker's data folder (`~/.local/share/claude-token-tracker/`, `%LOCALAPPDATA%\claude-token-tracker\`, `~/Library/Application Support/claude-token-tracker/`); both sides of a Windows + WSL computer are read. The bridge reads only what Claude Code passes it: no credentials, no network. The command points at this copy of `statusline.py`, so after moving the folder run `--install` again.

**How the share is estimated.** Each rise of the official percentage between two samples counts as Claude Code when this computer's Claude Code calls fall inside the gap: all of it for a gap of up to 10 minutes; for a longer gap, at most what the calls' API-equivalent cost explains at the percent-per-dollar rate measured on the short gaps; nothing for a gap with no Claude Code call (chat in a break, another device). The window start counts as 0 %. What it cannot see: chat used during a Claude Code turn counts as Claude Code; sessions run without the bridge (another computer, a side without the install) are missed; samples start at the install, so the first window is partial. API-key users get no percentages.

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
statusline.py       # Status line bridge: records the plan-limit percentages Claude Code shows
limits.py           # Claude Code's share of the 5-hour and weekly limits, from those samples
pricing.py          # Rate tables + cost math (the only file with price numbers)
dashboard.py        # Self-contained HTML dashboard generator (Chart.js + flatpickr inlined)
test_pricing.py     # Tests for pricing.py (stdlib unittest)
test_track_tokens.py  # Tests for parsing, de-duplication, sources, dates and projects
test_dashboard.py   # Tests for the dashboard page (date helpers run under node when installed)
test_app.py         # Tests for the desktop window (pywebview stubbed)
test_statusline.py  # Tests for the bridge, its install and the command in bash
test_limits.py      # Tests for the share estimate
build.py            # PyInstaller build script (macOS/Linux/Windows)
gen_icon.py         # App icon generator (macOS-only, uses AppKit)
assets/             # Chart.js, flatpickr, icons — inlined/bundled into the app
token_usage.json    # Generated report (gitignored)
```

## How numbers are counted

- **Each API call counts once.** Claude Code writes one transcript line per content block of a response, each repeating the call's usage, and a resumed session copies earlier lines into a new file. Lines are grouped by message id + request id across all files, keeping the line with the final output count. Lines Claude Code makes up itself (model `<synthetic>`, no API call behind them) are not counted.
- **Cost is API-equivalent** — what usage would cost at published list prices, not a subscription bill. Each call is priced at the rate in effect on its UTC date (`pricing.py` keeps dated rows and their sources), including cache writes (5 min / 1 h), cache reads, Haiku 5.5 prompt-length tiers, fast mode (2× on Opus 5.5, Opus 5 and Opus 4.8), US-only inference (1.1× on Claude 4.6 and later) and web search ($10 per 1,000 searches) when a transcript line records one.
- **Nothing is guessed.** An unknown model, fast mode on a model without a published multiplier, or an unknown cache tier is reported as unpriced: its tokens are counted, its cost is not.
- **Days are your local days** (the computer's time zone), in the CLI, the report and the dashboard. Projects are named after the working directory recorded in their transcripts, and the project table follows the date filter.

### What the transcripts do not show

Measured on 2026-10-13 against Claude Code's own `/cost` and `/stats`, and against ccusage, on the same closed days:

- **Calls without a transcript line are not counted.** Claude Code also makes side calls (on Haiku) that it bills but does not write to a transcript, and every web search is billed on one of them. On the measuring machine `/cost` was 8.6–12.4 % above the tracker for the same sessions. So web-search fees practically never appear here: 0 of 105,551 transcript calls carried a search.
- **The final output count is not always written.** Some subagent calls keep only the line written when the response started, so their output tokens are low. Every tool that reads transcripts shares this.
- **Only this computer.** History on another computer (a Mac, a second PC) is not in these folders, while the plan's 5-hour and weekly limits count every computer and claude.ai too. Claude Code's own `/cost`, `/stats` and `/usage` breakdown read only the side they run on.
- **`/stats` is about twice the tracker** because it adds every transcript line without removing the repeats and uses UTC days. ccusage gives the same tokens; its cost differs where its price table lacks a dated change.

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

Rates are USD per million tokens. `cacheWrite5m` / `cacheWrite1h` default to 1.25× / 2× `input`. `multiplier` (above 0, at most 10) scales every token cost. A row for an existing model replaces the built-in rates on every date. The file may be UTF-8 (with or without a BOM, as Windows Notepad saves it) or UTF-16. A malformed file is ignored with one warning on the terminal.

Run the tests with `python3 -m unittest` (`python -m unittest` on Windows). They need only the standard library; a few skip with a reason on Windows or without `node` / `python3-gi`.

No `LICENSE` file is present in this repository.
