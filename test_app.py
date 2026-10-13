#!/usr/bin/env python3
"""Tests for the desktop entry point, with a stand-in for pywebview. Run: python3 -m unittest test_app -v"""

from __future__ import annotations

import contextlib
import html as html_lib
import io
import logging
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from unittest import mock

import app
import track_tokens
from track_tokens import TranscriptsNotFound

try:  # GLib is how GNOME and most menus read .desktop files; the tests that use it skip without it.
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
except (ImportError, ValueError):
    Gio = GLib = None


class FakeWindow:
    def __init__(self):
        self.events = types.SimpleNamespace(closed=threading.Event())
        self.loaded_urls, self.pages, self.folder_files = [], [], []
        self.loaded = threading.Event()

    def load_url(self, url):
        # Read now: the folder goes when the window closes.
        self.loaded_urls.append(url)
        self.pages.append(Path(url).read_text(encoding="utf-8"))
        self.folder_files = sorted(p.name for p in Path(url).parent.iterdir())
        self.loaded.set()


class FakeWebview(types.ModuleType):
    """Records create_window and, at start(), what a real window would show: its first page, then the one func loads."""

    def __init__(self):
        super().__init__("webview")
        self.windows = []
        self.window = None
        self.first = self.loaded = None

    def create_window(self, title, **kwargs):
        self.windows.append(kwargs)
        self.window = FakeWindow()
        return self.window

    def start(self, func=None, **kwargs):
        self.first = Path(self.windows[-1]["url"]).read_text(encoding="utf-8")
        if func:
            func()
            if not self.window.loaded.wait(10):
                raise AssertionError("the window never got its page")
            self.loaded = self.window.pages[-1]


def allow_native_window(test: unittest.TestCase) -> None:
    """The fake window opens wherever the tests run, display or not."""
    patcher = mock.patch.object(app, "native_window_blocker", return_value=None)
    patcher.start()
    test.addCleanup(patcher.stop)


class TestNativeWindow(unittest.TestCase):
    def setUp(self):
        allow_native_window(self)

    def open(self, html: str) -> FakeWebview:
        fake = FakeWebview()
        with mock.patch.dict(sys.modules, {"webview": fake}):
            app.open_native_window(lambda: html)
        return fake

    def test_page_is_loaded_from_a_file_not_passed_inline(self):
        """html= goes through Qt setHtml / WebView2 NavigateToString, which show a blank page above 2 MB."""
        html = "<!DOCTYPE html><title>x</title>" + "x" * 3_000_000
        fake = self.open(html)
        (window,) = fake.windows
        self.assertNotIn("html", window)
        self.assertEqual(fake.loaded, html)

    def test_url_is_a_local_path_pywebview_serves(self):
        """A plain path makes pywebview serve its folder on 127.0.0.1; file:// is not loaded the same everywhere."""
        fake = self.open("<p>hi</p>")
        first, (dashboard,) = fake.windows[0]["url"], fake.window.loaded_urls
        for url in (first, dashboard):
            self.assertNotIn("://", url)
            self.assertTrue(Path(url).is_absolute())
        self.assertEqual(Path(first).parent, Path(dashboard).parent)

    def test_window_opens_on_a_scanning_page_before_the_scan_ends(self):
        """The packaged app showed nothing for 25 s (90 s cold) while it scanned; a second double-click scanned again."""
        scanned = threading.Event()
        seen = {}

        def make_html():
            scanned.set()
            return "<p>dashboard</p>"

        fake = FakeWebview()
        real_start = fake.start

        def start(func=None, **kwargs):
            seen["scanned_before_window"] = scanned.is_set()
            real_start(func, **kwargs)

        fake.start = start
        with mock.patch.dict(sys.modules, {"webview": fake}):
            app.open_native_window(make_html)
        self.assertFalse(seen["scanned_before_window"])
        self.assertIn("Scanning", fake.first)
        self.assertEqual(fake.loaded, "<p>dashboard</p>")

    def test_a_failed_scan_says_why_in_the_window(self):
        """Otherwise the window would say 'Scanning' forever."""
        def make_html():
            raise ValueError("bad <line>")

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            fake = self.open_with(make_html)
        self.assertIn("ValueError: bad &lt;line&gt;", fake.loaded)
        self.assertIn("Traceback", err.getvalue())

    def test_closing_the_window_mid_scan_does_not_wait_for_the_scan(self):
        release = threading.Event()
        self.addCleanup(release.set)
        seen = {}

        def make_html():
            seen["daemon"] = threading.current_thread().daemon
            release.wait(10)
            return "<p>late</p>"

        fake = FakeWebview()

        def start(func=None, **kwargs):
            func()
            fake.window.events.closed.set()

        fake.start = start
        began = time.monotonic()
        with mock.patch.dict(sys.modules, {"webview": fake}):
            app.open_native_window(make_html)
        self.assertLess(time.monotonic() - began, 5)
        for _ in range(100):
            if "daemon" in seen:
                break
            time.sleep(0.02)
        self.assertTrue(seen["daemon"])

    def open_with(self, make_html) -> FakeWebview:
        fake = FakeWebview()
        with mock.patch.dict(sys.modules, {"webview": fake}):
            app.open_native_window(make_html)
        return fake

    def test_page_folder_holds_only_the_pages_and_is_removed(self):
        """pywebview serves the whole folder, so it must be a private folder of its own."""
        fake = self.open("<p>hi</p>")
        self.assertEqual(len(fake.window.folder_files), 2)
        self.assertFalse(Path(fake.window.loaded_urls[0]).parent.exists())

    def test_folder_is_removed_when_the_window_fails(self):
        fake = FakeWebview()
        seen = {}

        def start(func=None, **kwargs):
            seen["folder"] = Path(fake.windows[-1]["url"]).parent
            raise RuntimeError("no display")

        fake.start = start
        with mock.patch.dict(sys.modules, {"webview": fake}), self.assertRaises(RuntimeError):
            app.open_native_window(lambda: "<p>hi</p>")
        self.assertFalse(seen["folder"].exists())

    def test_page_name_cannot_be_guessed(self):
        """The page is served on 127.0.0.1 while the window is open; another local user must not find it by name."""
        names = {Path(self.open("<p>hi</p>").window.loaded_urls[0]).name for _ in range(2)}
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertTrue(name.endswith(".html"))
            self.assertGreaterEqual(len(name), 20)


class TestNativeWindowBlocker(unittest.TestCase):
    """Qt aborts the whole process in C++ when it cannot open a window, so no except clause can reach the fallback."""

    def blocker(self, env: dict, lib: str | None = "libxcb-cursor.so.0", platform: str = "linux") -> str | None:
        return app.native_window_blocker(platform, env, lambda name: lib)

    def test_no_display_means_no_native_window(self):
        self.assertIn("no display", self.blocker({}))

    def test_x11_without_xcb_cursor_names_the_package(self):
        self.assertIn("sudo apt install libxcb-cursor0", self.blocker({"DISPLAY": ":0"}, lib=None))

    def test_x11_with_xcb_cursor_opens(self):
        self.assertIsNone(self.blocker({"DISPLAY": ":0"}))

    def test_wayland_does_not_need_xcb_cursor(self):
        self.assertIsNone(self.blocker({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}, lib=None))

    def test_a_platform_the_user_chose_is_respected(self):
        self.assertIsNone(self.blocker({"QT_QPA_PLATFORM": "offscreen"}, lib=None))

    def test_only_linux_is_checked(self):
        for platform in ("darwin", "win32"):
            self.assertIsNone(self.blocker({}, lib=None, platform=platform))

    def test_a_blocked_window_raises_before_making_a_folder(self):
        fake = FakeWebview()
        with mock.patch.dict(sys.modules, {"webview": fake}), \
                mock.patch.object(app, "native_window_blocker", return_value="no display"), \
                mock.patch.object(app.tempfile, "mkdtemp") as mkdtemp, \
                self.assertRaisesRegex(RuntimeError, "no display"):
            app.open_native_window(lambda: "<p>hi</p>")
        mkdtemp.assert_not_called()
        self.assertEqual(fake.windows, [])


class TestBackendLog(unittest.TestCase):
    """pywebview logs a full traceback for each GUI backend it cannot load, before the app's one-line fallback note."""

    def capture(self) -> io.StringIO:
        out = io.StringIO()
        handler = logging.StreamHandler(out)
        logger = logging.getLogger("pywebview")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        app.quiet_backend_tracebacks()
        return out

    def log_exception(self, message: str) -> None:
        try:
            raise ImportError("No module named 'qtpy'")
        except ImportError:
            logging.getLogger("pywebview").exception(message)

    def test_a_backend_that_cannot_load_is_one_line(self):
        out = self.capture()
        self.log_exception("QT cannot be loaded")
        self.assertEqual(out.getvalue(), "QT cannot be loaded\n")

    def test_other_errors_keep_their_traceback(self):
        out = self.capture()
        self.log_exception("Error occurred in window")
        self.assertIn("Traceback", out.getvalue())

    def test_the_window_installs_it(self):
        allow_native_window(self)
        with mock.patch.dict(sys.modules, {"webview": FakeWebview()}), \
                mock.patch.object(app, "quiet_backend_tracebacks") as quiet:
            app.open_native_window(lambda: "<p>hi</p>")
        quiet.assert_called_once_with()


class TestStalePages(unittest.TestCase):
    """Cmd+Q on macOS ends the process inside webview.start(), so its folder outlives it; the next launch removes it."""

    def setUp(self):
        allow_native_window(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        patcher = mock.patch.object(tempfile, "tempdir", tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def folder(self, name: str, age_hours: float) -> Path:
        folder = self.base / name
        folder.mkdir()
        (folder / "page.html").write_text("x", encoding="utf-8")
        then = time.time() - age_hours * 3600
        os.utime(folder, (then, then))
        return folder

    def test_old_page_folders_go_and_the_rest_stays(self):
        old = self.folder("claude-token-tracker-old", age_hours=25)
        running = self.folder("claude-token-tracker-running", age_hours=1)
        unrelated = self.folder("someone-else-old", age_hours=48)
        fake = FakeWebview()
        with mock.patch.dict(sys.modules, {"webview": fake}):
            app.open_native_window(lambda: "<p>hi</p>")
        self.assertFalse(old.exists())
        self.assertTrue(running.exists())
        self.assertTrue(unrelated.exists())


class TestBrowserFallback(unittest.TestCase):
    """The fallback page holds every project path and cost: it gets the same private folder as the native window."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        patcher = mock.patch.object(tempfile, "tempdir", tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open(self, html: str) -> Path:
        with mock.patch.object(app.webbrowser, "open") as opened, contextlib.redirect_stdout(io.StringIO()):
            app.open_in_browser(html)
        (uri,), _ = opened.call_args
        return Path(urllib.request.url2pathname(urllib.parse.urlparse(uri).path))

    def test_page_is_in_a_private_folder_with_a_random_name(self):
        pages = [self.open("<p>hi</p>") for _ in range(2)]
        self.assertNotEqual(pages[0], pages[1])
        for page in pages:
            self.assertEqual(page.read_text(encoding="utf-8"), "<p>hi</p>")
            self.assertEqual(page.parent.parent, self.base)
            self.assertTrue(page.parent.name.startswith(app.PAGE_FOLDER_PREFIX))
            if os.name == "posix":
                self.assertEqual(page.parent.stat().st_mode & 0o077, 0)

    def test_a_name_someone_else_holds_does_not_stop_it(self):
        """In a shared /tmp the old fixed name could belong to another user: writing it raised and nothing opened."""
        (self.base / "claude_token_report.html").mkdir()
        self.assertTrue(self.open("<p>hi</p>").is_file())

    def test_old_fallback_pages_age_out(self):
        old = self.base / (app.PAGE_FOLDER_PREFIX + "old")
        old.mkdir()
        then = time.time() - 25 * 3600
        os.utime(old, (then, then))
        self.open("<p>hi</p>")
        self.assertFalse(old.exists())


class TestNoTranscripts(unittest.TestCase):
    def test_app_explains_instead_of_closing(self):
        """The packaged app has no console: exiting on 'not found' showed nothing at all."""
        folders = [Path("/home/u/.claude/projects"), Path("/home/u/<odd>/projects")]
        with mock.patch.object(app, "scan_and_report", side_effect=TranscriptsNotFound(folders)), \
                mock.patch.object(app, "open_native_window") as window, \
                mock.patch.object(app.sys, "argv", ["app.py"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            app.main()
            (make_html,), _ = window.call_args
            page = make_html()
        self.assertIn(str(folders[0]), page)
        self.assertIn(html_lib.escape(str(folders[1])), page)
        self.assertIn("&lt;odd&gt;", page)
        self.assertIn("CLAUDE_CONFIG_DIR", page)


class TestFallbackScan(unittest.TestCase):
    def test_the_browser_gets_the_page_the_window_already_scanned(self):
        """A window that dies after its scan must not cost a second scan of the whole history."""
        def window(make_html):
            make_html()
            raise RuntimeError("window died")

        with mock.patch.object(app, "scan_and_report", return_value=({}, "<p>dashboard</p>")) as scan, \
                mock.patch.object(app, "open_native_window", side_effect=window), \
                mock.patch.object(app, "open_in_browser") as browser, \
                mock.patch.object(app.sys, "argv", ["app.py"]), contextlib.redirect_stdout(io.StringIO()):
            app.main()
        scan.assert_called_once_with()
        browser.assert_called_once_with("<p>dashboard</p>")


class TestConsole(unittest.TestCase):
    def test_fallback_survives_a_console_that_cannot_encode_the_path(self):
        """A user named Nguyễn has a temp path cp1252 cannot encode; printing it to a redirected stdout raised (WIN-4)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        temp = Path(tmp.name) / "tạm thời"
        temp.mkdir()
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="cp1252")
        missing = TranscriptsNotFound([Path(tmp.name) / "projects"])
        with mock.patch.object(tempfile, "tempdir", str(temp)), \
                mock.patch.object(app, "scan_and_report", side_effect=missing), \
                mock.patch.object(app, "open_native_window", side_effect=RuntimeError("no display")), \
                mock.patch.object(app.webbrowser, "open") as opened, \
                mock.patch.object(app.sys, "argv", ["app.py"]), \
                mock.patch.object(app.sys, "stdout", out), contextlib.redirect_stderr(io.StringIO()):
            app.main()
        out.flush()
        opened.assert_called_once()
        self.assertIn("t?m th?i".encode(), raw.getvalue())


class TestDesktopEntry(unittest.TestCase):
    """The Desktop Entry spec quotes an Exec= argument with a space; a home folder with one dropped the entry."""

    ODD_FOLDER = 'h$o"m`e\\d x'

    def test_a_path_with_a_space_is_quoted(self):
        launcher = PurePosixPath("/home/a b/.local/lib/claude-token-tracker/AppRun")
        entry = app.desktop_entry(launcher, "/home/a b/icon.png")
        self.assertIn('Exec="/home/a b/.local/lib/claude-token-tracker/AppRun"\n', entry)
        self.assertIn("Icon=/home/a b/icon.png\n", entry)

    @unittest.skipIf(GLib is None, "GLib (python3-gi) is not installed")
    def test_glib_reads_back_the_exact_path(self):
        for path in ("/home/a b/AppRun", f"/tmp/{self.ODD_FOLDER}/AppRun", "/tmp/100% x/AppRun"):
            with self.subTest(path=path):
                keys = GLib.KeyFile()
                entry = app.desktop_entry(PurePosixPath(path), "x").encode()
                keys.load_from_data(entry.decode(), len(entry), GLib.KeyFileFlags.NONE)
                command = keys.get_string("Desktop Entry", "Exec").replace("%%", "%")
                self.assertEqual(GLib.shell_parse_argv(command)[1], [path])

    @unittest.skipIf(Gio is None, "GLib (python3-gi) is not installed")
    def test_the_menu_accepts_the_entry(self):
        """GLib drops an entry whose launcher it cannot find, which is what an unquoted space did."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for folder in ("home with space", self.ODD_FOLDER):
            with self.subTest(folder=folder):
                launcher = Path(tmp.name) / folder / "AppRun"
                launcher.parent.mkdir()
                launcher.write_text("#!/bin/sh\n", encoding="utf-8")
                launcher.chmod(0o755)
                desktop = Path(tmp.name) / "claude-token-tracker.desktop"
                desktop.write_text(app.desktop_entry(launcher, "utilities-system-monitor"), encoding="utf-8")
                self.assertIsNotNone(Gio.DesktopAppInfo.new_from_filename(str(desktop)))

    def test_install_outside_the_appimage_exits_1(self):
        """It printed that it only works from the .AppImage, then exited 0, so a script thought it had installed."""
        err = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(app.sys, "argv", ["app.py", "--install"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as exit_:
            os.environ.pop("APPIMAGE", None)
            app.main()
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn(".AppImage", err.getvalue())


class TestReportFile(unittest.TestCase):
    def test_scan_survives_a_report_file_it_cannot_write(self):
        """An AppImage runs from a read-only mount: a failed token_usage.json write must not stop the window."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        blocker = Path(tmp.name) / "not-a-folder"
        blocker.write_text("", encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(track_tokens, "collect_all_entries", return_value=([], {})), \
                mock.patch.object(track_tokens, "OUTPUT_FILE", blocker / "token_usage.json"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            _report, html = app.scan_and_report()
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("could not save", err.getvalue())


if __name__ == "__main__":
    unittest.main()
