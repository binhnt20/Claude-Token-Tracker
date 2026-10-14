#!/usr/bin/env python3
"""
Claude Code Token Tracker - Desktop App
Scans local Claude Code transcripts and shows an interactive dashboard in a native window.
"""
from __future__ import annotations

import ctypes.util
import html as html_lib
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from pathlib import Path, PurePath
from typing import Callable, Mapping

import track_tokens
from track_tokens import TranscriptsNotFound, build_report, print_summary, save_report
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


def _desktop_string(value: str) -> str:
    """A Desktop Entry string value, with backslash and the control characters escaped."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")


def desktop_entry(launcher: PurePath, icon: str) -> str:
    """The .desktop file for the menu. Exec= quotes the launcher, so a home folder with a space or $ still runs.

    Inside the quotes the spec reserves " ` $ \\; the string escape applies on top, and % is doubled (field codes).
    """
    exec_arg = '"' + re.sub(r'(["`$\\])', r"\\\1", str(launcher)) + '"'
    return ("[Desktop Entry]\n"
            f"Name={APP_NAME}\n"
            "Comment=Claude Code token usage dashboard\n"
            f"Exec={_desktop_string(exec_arg).replace('%', '%%')}\n"
            f"Icon={_desktop_string(icon)}\n"
            "Type=Application\n"
            "Categories=Utility;Development;\n"
            "Terminal=false\n")


def _install_desktop_entry() -> bool:
    """Install the app into ~/.local so it launches from the menu without FUSE.

    The AppImage is extracted once into ~/.local/lib/<id> and the .desktop
    entry points at the extracted launcher, so no FUSE mount is needed at
    click time (and startup is faster than mounting on every launch).
    """
    appimage = os.environ.get("APPIMAGE")
    if not appimage:
        print("  --install only works when running from the .AppImage.", file=sys.stderr)
        return False

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
    desktop.write_text(desktop_entry(launcher, icon_value), encoding="utf-8")
    print(f"  Installed to: {INSTALL_DIR}")
    print(f"  Menu entry:   {desktop}")
    print(f'  Search "{APP_NAME}" in your application menu to launch it.')
    return True


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
    all_entries, project_totals = track_tokens.collect_all_entries()
    print(f"Found {len(all_entries):,} responses across {len(project_totals)} projects")

    report = build_report(all_entries, project_totals, date_filter=None)

    save_report(report)
    print_summary(report)
    return report, generate_html(report)


def not_found_html(exc: TranscriptsNotFound) -> str:
    """A page that says where transcripts were looked for: the packaged app has no console to print it to."""
    folders = "".join(f"<li><code>{html_lib.escape(str(c))}</code></li>" for c in exc.candidates)
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Claude Code Token Usage</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:40px;line-height:1.6}</style></head><body>"
            "<h1>No Claude Code transcripts found</h1><p>Looked in:</p>"
            f"<ul>{folders}</ul><p>Use Claude Code once, or set <code>CLAUDE_CONFIG_DIR</code> to the folder it uses, "
            "then open this app again.</p></body></html>")


def error_html(exc: Exception) -> str:
    """Shown in the window when building the dashboard failed, instead of 'Scanning' forever."""
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Claude Code Token Usage</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:40px;line-height:1.6}</style></head><body>"
            "<h1>Could not build the dashboard</h1>"
            f"<pre>{html_lib.escape(f'{type(exc).__name__}: {exc}')}</pre>"
            f"<p>Run <code>{track_tokens.python_command()} track_tokens.py</code> in a terminal "
            "to see the full error.</p></body></html>")


# Same colours as the dashboard, so the switch to it does not flash.
SCANNING_HTML = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Claude Code Token Usage</title>"
                 "<style>body{font-family:system-ui,sans-serif;margin:40px;line-height:1.6;background:#0f172a;"
                 "color:#f1f5f9}@media (prefers-color-scheme: light){body{background:#f1f5f9;color:#1e293b}}</style>"
                 "</head><body><h1>Scanning Claude Code transcripts\u2026</h1>"
                 "<p>The dashboard opens here when the scan is done. A first scan of a long history can take a minute."
                 "</p></body></html>")


def scan_page() -> str:
    """The dashboard, or a page that says where transcripts were looked for."""
    try:
        _report, html = scan_and_report()
    except TranscriptsNotFound as exc:
        print(exc, file=sys.stderr)
        html = not_found_html(exc)
    return html


def once(make: Callable[[], str]) -> Callable[[], str]:
    """make, run at most once: a fallback after the window already scanned reuses the page."""
    lock, result = threading.Lock(), []

    def call() -> str:
        with lock:
            if not result:
                result.append(make())
            return result[0]
    return call


PAGE_FOLDER_PREFIX = "claude-token-tracker-"
STALE_PAGE_SECONDS = 24 * 60 * 60


def _remove_stale_page_folders() -> None:
    """Delete page folders earlier runs left behind: every browser fallback's, and a native window's after Cmd+Q on
    macOS or a kill ended the process inside webview.start().

    A day old is old enough not to be a window that is still open.
    """
    now = time.time()
    for folder in Path(tempfile.gettempdir()).glob(PAGE_FOLDER_PREFIX + "*"):
        try:
            if folder.is_dir() and not folder.is_symlink() and now - folder.stat().st_mtime > STALE_PAGE_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def native_window_blocker(platform: str, env: Mapping[str, str], find_library: Callable[[str], str | None]) -> str | None:
    """Why Qt cannot open a window here, or None. Qt aborts the process when it fails, so it must not be tried.

    A QT_QPA_PLATFORM the user set is theirs to answer for. Wayland needs no extra library; X11 needs libxcb-cursor,
    which Ubuntu does not install by default and PySide6 does not bundle.
    """
    if not platform.startswith("linux") or env.get("QT_QPA_PLATFORM"):
        return None
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        return "no display: DISPLAY and WAYLAND_DISPLAY are not set"
    if not env.get("WAYLAND_DISPLAY") and find_library("xcb-cursor") is None:
        return "Qt needs libxcb-cursor0 to open a window on X11: sudo apt install libxcb-cursor0"
    return None


class _OneLineBackendErrors(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage().endswith("cannot be loaded"):
            record.exc_info = record.exc_text = None
        return True


_ONE_LINE_BACKEND_ERRORS = _OneLineBackendErrors()


def quiet_backend_tracebacks() -> None:
    """pywebview logs a traceback per GUI backend it cannot load; its one-line message and the fallback note say enough."""
    logging.getLogger("pywebview").addFilter(_ONE_LINE_BACKEND_ERRORS)


def _write_page(folder: Path, html: str) -> Path:
    page = folder / f"{secrets.token_urlsafe(16)}.html"
    page.write_text(html, encoding="utf-8")
    return page


def open_native_window(make_html: Callable[[], str]):
    """Open the dashboard in a native OS window using pywebview.

    The window opens at once on a "Scanning" page and make_html runs behind it, so a long first scan is not a minute
    with nothing on screen. It runs on a daemon thread: closing the window mid-scan ends the app.

    Pages are loaded from files, not passed as html=: that goes through Qt setHtml / WebView2 NavigateToString,
    which show a blank window once the page passes 2 MB. pywebview serves a plain path's folder on 127.0.0.1, so the
    pages get a private folder of their own, removed when the window closes, and random names, so another local user
    cannot read them from the server by guessing.
    """
    import webview

    reason = native_window_blocker(sys.platform, os.environ, ctypes.util.find_library)
    if reason:
        raise RuntimeError(reason)
    if sys.platform == "darwin":
        _setup_macos_dock()

    _remove_stale_page_folders()
    folder = Path(tempfile.mkdtemp(prefix=PAGE_FOLDER_PREFIX))
    try:
        window = webview.create_window(
            APP_NAME,
            url=str(_write_page(folder, SCANNING_HTML)),
            width=1200,
            height=800,
            min_size=(800, 600),
        )

        def show_page():
            try:
                html = make_html()
            except Exception as exc:
                traceback.print_exc()
                html = error_html(exc)
            if not window.events.closed.is_set():
                window.load_url(str(_write_page(folder, html)))

        def scan_behind_the_window():
            threading.Thread(target=show_page, name="scan", daemon=True).start()

        quiet_backend_tracebacks()
        # On Linux we ship the self-contained Qt/QtWebEngine backend; force it so
        # pywebview does not fall back to the system GTK/WebKit stack.
        if sys.platform.startswith("linux"):
            webview.start(scan_behind_the_window, gui="qt")
        else:
            webview.start(scan_behind_the_window)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def open_in_browser(html: str):
    """Fallback: open the page in the default browser, from a private folder like the native window's.

    The browser reads the file after this returns, so the folder stays and ages out with the others.
    """
    _remove_stale_page_folders()
    html_path = Path(tempfile.mkdtemp(prefix=PAGE_FOLDER_PREFIX)) / f"{secrets.token_urlsafe(16)}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"  Opening: {html_path}")
    webbrowser.open(html_path.as_uri())


def main():
    track_tokens.safe_console()
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(f"Usage: {APP_NAME.lower().replace(' ', '-')} [--install | --uninstall]")
        print("  Opens an interactive dashboard with date filtering in the UI.")
        print("  --install    Add this app to the Linux application menu (run once).")
        print("  --uninstall  Remove it from the application menu.")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        sys.exit(0 if _install_desktop_entry() else 1)

    if len(sys.argv) > 1 and sys.argv[1] == "--uninstall":
        _uninstall_desktop_entry()
        sys.exit(0)

    page = once(scan_page)
    try:
        open_native_window(page)
    except Exception as exc:
        print(f"  Native window unavailable ({exc}); opening in browser...")
        open_in_browser(page())


if __name__ == "__main__":
    main()
