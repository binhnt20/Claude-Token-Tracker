#!/usr/bin/env python3
"""Tests for the desktop entry point, with a stand-in for pywebview. Run: python3 -m unittest test_app -v"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import app
import track_tokens
from track_tokens import TranscriptsNotFound


class FakeWebview(types.ModuleType):
    """Records create_window and, at start(), what a real window would load."""

    def __init__(self):
        super().__init__("webview")
        self.windows = []
        self.loaded = None

    def create_window(self, title, **kwargs):
        self.windows.append(kwargs)

    def start(self, **kwargs):
        url = self.windows[-1].get("url")
        self.loaded = Path(url).read_text(encoding="utf-8") if url else None


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
            app.open_native_window(html)
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
        (window,) = self.open("<p>hi</p>").windows
        self.assertNotIn("://", window["url"])
        self.assertTrue(Path(window["url"]).is_absolute())

    def test_page_folder_holds_only_the_page_and_is_removed(self):
        """pywebview serves the whole folder, so it must be a private folder of its own."""
        fake = FakeWebview()
        seen = {}

        def start(**kwargs):
            page = Path(fake.windows[-1]["url"])
            seen["folder"], seen["files"] = page.parent, [p.name for p in page.parent.iterdir()]

        fake.start = start
        with mock.patch.dict(sys.modules, {"webview": fake}):
            app.open_native_window("<p>hi</p>")
        self.assertEqual(len(seen["files"]), 1)
        self.assertFalse(seen["folder"].exists())

    def test_folder_is_removed_when_the_window_fails(self):
        fake = FakeWebview()
        seen = {}

        def start(**kwargs):
            seen["folder"] = Path(fake.windows[-1]["url"]).parent
            raise RuntimeError("no display")

        fake.start = start
        with mock.patch.dict(sys.modules, {"webview": fake}), self.assertRaises(RuntimeError):
            app.open_native_window("<p>hi</p>")
        self.assertFalse(seen["folder"].exists())

    def test_page_name_cannot_be_guessed(self):
        """The page is served on 127.0.0.1 while the window is open; another local user must not find it by name."""
        names = {Path(self.open("<p>hi</p>").windows[0]["url"]).name for _ in range(2)}
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
            app.open_native_window("<p>hi</p>")
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
            app.open_native_window("<p>hi</p>")
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
            app.open_native_window("<p>hi</p>")
        self.assertFalse(old.exists())
        self.assertTrue(running.exists())
        self.assertTrue(unrelated.exists())


class TestNoTranscripts(unittest.TestCase):
    def test_app_explains_instead_of_closing(self):
        """The packaged app has no console: exiting on 'not found' showed nothing at all."""
        missing = TranscriptsNotFound([Path("/home/u/.claude/projects"), Path("/home/u/<odd>/projects")])
        with mock.patch.object(app, "scan_and_report", side_effect=missing), \
                mock.patch.object(app, "open_native_window") as window, \
                mock.patch.object(app.sys, "argv", ["app.py"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            app.main()
        (html,), _ = window.call_args
        self.assertIn("/home/u/.claude/projects", html)
        self.assertIn("/home/u/&lt;odd&gt;/projects", html)
        self.assertIn("CLAUDE_CONFIG_DIR", html)


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
