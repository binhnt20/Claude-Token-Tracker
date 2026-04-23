#!/usr/bin/env python3
"""
Claude Code Token Tracker - Desktop App
Scans local Claude Code transcripts and shows an interactive dashboard in a native window.
"""
from __future__ import annotations

import json
import sys
import tempfile
import webbrowser
from pathlib import Path

from track_tokens import collect_all_entries, build_report, print_summary, OUTPUT_FILE
from dashboard import generate_html, ASSETS_DIR

APP_NAME = "Claude Token Tracker"


def _setup_macos_dock():
    """Set dock name and icon on macOS."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        app = NSApplication.sharedApplication()

        # Set dock name
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info:
            info["CFBundleName"] = APP_NAME

        # Set dock icon
        icon_path = ASSETS_DIR / "icon.png"
        if icon_path.exists():
            icon = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if icon:
                app.setApplicationIconImage_(icon)
    except Exception:
        pass


def scan_and_report() -> tuple[dict, str]:
    """Scan transcripts, build report (no date filter - all data), return (report, html)."""
    print("Scanning Claude Code transcripts...")
    all_entries, project_totals = collect_all_entries()
    print(f"Found {len(all_entries):,} responses across {len(project_totals)} projects")

    report = build_report(all_entries, project_totals, date_filter=None)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print_summary(report)
    return report, generate_html(report)


def open_native_window(html: str):
    """Open dashboard in a native OS window using pywebview."""
    import webview

    if sys.platform == "darwin":
        _setup_macos_dock()

    webview.create_window(
        APP_NAME,
        html=html,
        width=1200,
        height=800,
        min_size=(800, 600),
    )
    webview.start()


def open_in_browser(html: str):
    """Fallback: save HTML to temp file and open in default browser."""
    html_path = Path(tempfile.gettempdir()) / "claude_token_report.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"  Opening: {html_path}")
    webbrowser.open(html_path.as_uri())


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(f"Usage: {APP_NAME.lower().replace(' ', '-')}")
        print("  Opens an interactive dashboard with date filtering in the UI.")
        sys.exit(0)

    _report, html = scan_and_report()

    try:
        open_native_window(html)
    except ImportError:
        print("  pywebview not found, opening in browser...")
        open_in_browser(html)


if __name__ == "__main__":
    main()
