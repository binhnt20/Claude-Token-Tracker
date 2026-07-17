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


INSTALL_DIR = Path.home() / ".local" / "lib" / DESKTOP_ID
APPS_DIR = Path.home() / ".local" / "share" / "applications"
ICONS_DIR = Path.home() / ".local" / "share" / "icons"


def _install_desktop_entry():
    """Install the app into ~/.local so it launches from the menu without FUSE.

    The AppImage is extracted once into ~/.local/lib/<id> and the .desktop
    entry points at the extracted launcher, so no FUSE mount is needed at
    click time (and startup is faster than mounting on every launch).
    """
    appimage = os.environ.get("APPIMAGE")
    if not appimage:
        print("  --install only works when running from the .AppImage.")
        return

    import shutil
    import subprocess

    APPS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    INSTALL_DIR.parent.mkdir(parents=True, exist_ok=True)

    # Extract the AppImage once (--appimage-extract needs no FUSE).
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
    print("  Extracting app (this can take a moment)...")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [appimage, "--appimage-extract"],
            cwd=tmp, check=True, stdout=subprocess.DEVNULL,
        )
        shutil.move(str(Path(tmp) / "squashfs-root"), str(INSTALL_DIR))

    launcher = INSTALL_DIR / "AppRun"
    launcher.chmod(0o755)

    icon_dest = ICONS_DIR / f"{DESKTOP_ID}.png"
    icon_src = INSTALL_DIR / f"{DESKTOP_ID}.png"
    if icon_src.exists():
        shutil.copy2(icon_src, icon_dest)
    icon_value = str(icon_dest) if icon_dest.exists() else "utilities-system-monitor"

    desktop = APPS_DIR / f"{DESKTOP_ID}.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        f"Name={APP_NAME}\n"
        "Comment=Claude Code token usage dashboard\n"
        f"Exec={launcher}\n"
        f"Icon={icon_value}\n"
        "Type=Application\n"
        "Categories=Utility;Development;\n"
        "Terminal=false\n",
        encoding="utf-8",
    )
    print(f"  Installed to: {INSTALL_DIR}")
    print(f"  Menu entry:   {desktop}")
    print(f'  Search "{APP_NAME}" in your application menu to launch it.')


def _uninstall_desktop_entry():
    """Remove the app files and menu entry created by --install."""
    import shutil
    removed = False
    for p in (
        APPS_DIR / f"{DESKTOP_ID}.desktop",
        ICONS_DIR / f"{DESKTOP_ID}.png",
    ):
        if p.exists():
            p.unlink()
            removed = True
            print(f"  Removed: {p}")
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
        removed = True
        print(f"  Removed: {INSTALL_DIR}")
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
