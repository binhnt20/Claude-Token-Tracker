#!/usr/bin/env python3
"""
Claude Code Token Tracker - Desktop App
Scans local Claude Code transcripts and shows an interactive dashboard in a native window.
"""
from __future__ import annotations

import json
import os
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


DESKTOP_ID = "claude-token-tracker"


def _install_desktop_entry():
    """Register the AppImage in the Linux application menu (~/.local/share)."""
    appimage = os.environ.get("APPIMAGE")
    if not appimage:
        print("  --install only works when running from the .AppImage.")
        return

    apps_dir = Path.home() / ".local" / "share" / "applications"
    icons_dir = Path.home() / ".local" / "share" / "icons"
    apps_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)

    icon_dest = icons_dir / f"{DESKTOP_ID}.png"
    icon_src = ASSETS_DIR / "icon.png"
    if icon_src.exists():
        import shutil
        shutil.copy2(icon_src, icon_dest)
    icon_value = str(icon_dest) if icon_dest.exists() else "utilities-system-monitor"

    desktop = apps_dir / f"{DESKTOP_ID}.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        f"Name={APP_NAME}\n"
        "Comment=Claude Code token usage dashboard\n"
        f"Exec={appimage}\n"
        f"Icon={icon_value}\n"
        "Type=Application\n"
        "Categories=Utility;Development;\n"
        "Terminal=false\n",
        encoding="utf-8",
    )
    print(f"  Installed: {desktop}")
    print(f'  Search "{APP_NAME}" in your application menu to launch it.')
    print("  If it does not launch from the menu, install FUSE: sudo apt install libfuse2t64")


def _uninstall_desktop_entry():
    """Remove the Linux application-menu entry created by --install."""
    removed = False
    for p in (
        Path.home() / ".local" / "share" / "applications" / f"{DESKTOP_ID}.desktop",
        Path.home() / ".local" / "share" / "icons" / f"{DESKTOP_ID}.png",
    ):
        if p.exists():
            p.unlink()
            removed = True
            print(f"  Removed: {p}")
    if not removed:
        print("  Nothing to remove.")


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
    # On Linux we ship the self-contained Qt/QtWebEngine backend; force it so
    # pywebview does not fall back to the system GTK/WebKit stack.
    if sys.platform.startswith("linux"):
        webview.start(gui="qt")
    else:
        webview.start()


def open_in_browser(html: str):
    """Fallback: save HTML to temp file and open in default browser."""
    html_path = Path(tempfile.gettempdir()) / "claude_token_report.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"  Opening: {html_path}")
    webbrowser.open(html_path.as_uri())


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(f"Usage: {APP_NAME.lower().replace(' ', '-')} [--install | --uninstall]")
        print("  Opens an interactive dashboard with date filtering in the UI.")
        print("  --install    Add this app to the Linux application menu (run once).")
        print("  --uninstall  Remove it from the application menu.")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        _install_desktop_entry()
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--uninstall":
        _uninstall_desktop_entry()
        sys.exit(0)

    _report, html = scan_and_report()

    try:
        open_native_window(html)
    except Exception as exc:
        print(f"  Native window unavailable ({exc}); opening in browser...")
        open_in_browser(html)


if __name__ == "__main__":
    main()
