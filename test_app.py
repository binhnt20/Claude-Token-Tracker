#!/usr/bin/env python3
"""Tests for the desktop entry point, with a stand-in for pywebview. Run: python3 -m unittest test_app -v"""

from __future__ import annotations

import contextlib
import io
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import app
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


class TestNativeWindow(unittest.TestCase):
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
            seen["folder"], seen["files"] = page.parent, sorted(p.name for p in page.parent.iterdir())

        fake.start = start
        with mock.patch.dict(sys.modules, {"webview": fake}):
            app.open_native_window("<p>hi</p>")
        self.assertEqual(seen["files"], ["index.html"])
        self.assertFalse(seen["folder"].exists())


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


if __name__ == "__main__":
    unittest.main()
