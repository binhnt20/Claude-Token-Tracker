#!/usr/bin/env python3
"""Tests for the dashboard page. Run: python3 -m unittest test_dashboard -v

The JS helpers run under node when it is installed; the rest checks the generated HTML.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from dashboard import DATE_HELPERS_JS, PROJECT_HELPERS_JS, generate_html

NODE = shutil.which("node")


def run_node(script: str, tz: str = "UTC") -> subprocess.CompletedProcess:
    return subprocess.run([NODE, "-e", script], env={**os.environ, "TZ": tz},
                          capture_output=True, text=True, timeout=30)


def bucket(tokens: int, requests: int, cost: float | None, unpriced: int = 0) -> dict:
    return {"input_tokens": tokens, "output_tokens": 0, "cache_creation_input_tokens": 0,
            "cache_creation_5m_tokens": 0, "cache_creation_1h_tokens": 0, "cache_read_input_tokens": 0,
            "requests": requests, "unpriced_requests": unpriced, "cost": None if cost is None else {"total": cost}}


MINIMAL_REPORT = {"summary": {}, "by_date": {}, "by_project": {}, "by_date_project": {}}


@unittest.skipIf(NODE is None, "node is not installed")
class TestDateHelpers(unittest.TestCase):
    def test_date_helpers_east_of_utc(self):
        """Local midnight east of UTC is the previous day in UTC; toISOString-based dates slipped a day."""
        result = run_node(DATE_HELPERS_JS + """
            const assert = require('assert');
            assert.strictEqual(isoDate(addDays(toDate('2026-10-13'), -1)), '2026-10-12');
            assert.strictEqual(isoDate(toDate('2026-10-13')), '2026-10-13');
            assert.strictEqual(isoDate(addDays(toDate('2026-10-31'), 1)), '2026-11-01');
        """, tz="Asia/Ho_Chi_Minh")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_date_helpers_west_of_utc(self):
        result = run_node(DATE_HELPERS_JS + """
            const assert = require('assert');
            assert.strictEqual(isoDate(toDate('2026-03-08')), '2026-03-08');
            assert.strictEqual(isoDate(addDays(toDate('2026-03-08'), 1)), '2026-03-09');
        """, tz="America/Los_Angeles")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_today_is_the_local_calendar_day(self):
        result = run_node(DATE_HELPERS_JS + """
            const d = new Date();
            const expected = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-'
                + String(d.getDate()).padStart(2, '0');
            require('assert').strictEqual(localToday(), expected);
        """, tz="Pacific/Kiritimati")
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipIf(NODE is None, "node is not installed")
class TestProjectHelpers(unittest.TestCase):
    BY_DATE_PROJECT = {
        "2026-10-10": {"/a": bucket(100, 2, 1.5), "/b": bucket(10, 1, None, unpriced=1)},
        "2026-10-11": {"/a": bucket(50, 1, 0.5, unpriced=0), "/b": bucket(5, 1, 0.25)},
        "2026-10-12": {"/c": bucket(7, 1, None, unpriced=1)},
    }

    def sum_projects(self, dates):
        result = run_node(PROJECT_HELPERS_JS + f"""
            const out = sumProjects({json.dumps(self.BY_DATE_PROJECT)}, {json.dumps(dates)});
            console.log(JSON.stringify(out));
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {p["name"]: p for p in json.loads(result.stdout)}

    def test_only_filtered_dates_count(self):
        projects = self.sum_projects(["2026-10-11"])
        self.assertEqual(set(projects), {"/a", "/b"})
        self.assertEqual(projects["/a"]["total"], 50)
        self.assertEqual(projects["/a"]["requests"], 1)

    def test_cost_sums_priced_days(self):
        projects = self.sum_projects(["2026-10-10", "2026-10-11"])
        self.assertAlmostEqual(projects["/a"]["cost"], 2.0)
        self.assertAlmostEqual(projects["/b"]["cost"], 0.25)
        self.assertEqual(projects["/b"]["unpricedRequests"], 1)

    def test_all_unpriced_is_null_not_zero(self):
        projects = self.sum_projects(["2026-10-12"])
        self.assertIsNone(projects["/c"]["cost"])


class TestGeneratedHtml(unittest.TestCase):
    def setUp(self):
        self.html = generate_html(MINIMAL_REPORT)

    def test_html_builds_without_assets(self):
        self.assertIn('<canvas id="dailyChart">', self.html)

    def test_helpers_are_inlined(self):
        self.assertIn(DATE_HELPERS_JS, self.html)
        self.assertIn(PROJECT_HELPERS_JS, self.html)

    def test_today_is_not_last_data_day(self):
        self.assertIn("localToday()", self.html)
        self.assertNotIn("allDates[allDates.length - 1] : isoDate", self.html)

    def test_projects_use_by_date_project(self):
        self.assertIn("RAW.by_date_project", self.html)
        self.assertNotIn("all-time project breakdown", self.html)

    def test_current_models_have_colors(self):
        for model in ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-haiku-5-5",
                      "claude-fable-5-1"):
            self.assertIn(f"'{model}':", self.html)


if __name__ == "__main__":
    unittest.main()
