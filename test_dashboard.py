#!/usr/bin/env python3
"""Tests for the dashboard page. Run: python3 -m unittest test_dashboard -v

The JS helpers run under node when it is installed; the rest checks the generated HTML.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from unittest import mock

import dashboard
from dashboard import DATE_HELPERS_JS, PROJECT_HELPERS_JS, generate_html

NODE = shutil.which("node")


def run_node(script: str, tz: str = "UTC") -> subprocess.CompletedProcess:
    return subprocess.run([NODE, "-e", script], env={**os.environ, "TZ": tz},
                          capture_output=True, text=True, timeout=30)


def bucket(tokens: int, requests: int, cost: float | None, unpriced: int = 0) -> dict:
    return {"input_tokens": tokens, "output_tokens": 0, "cache_creation_input_tokens": 0,
            "cache_creation_5m_tokens": 0, "cache_creation_1h_tokens": 0, "cache_read_input_tokens": 0,
            "requests": requests, "unpriced_requests": unpriced, "cost": None if cost is None else {"total": cost}}


MINIMAL_REPORT = {"summary": {}, "by_date": {}, "by_project": {}, "by_date_project": {},
                  "generated_at": "2026-10-13T00:00:00+00:00"}


def embedded_data(html: str) -> dict:
    """The report object the page reads, as embedded in its script."""
    return json.loads(re.search(r"^const RAW = (.*);$", html, re.M).group(1))


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

    def test_range_for_each_button(self):
        """The quick buttons take today as an argument, so a page left open past midnight moves with the clock."""
        result = run_node(DATE_HELPERS_JS + """
            const assert = require('assert');
            const few = ['2026-10-01', '2026-10-12'];
            assert.deepStrictEqual(rangeFor(1, few, '2026-10-13'), ['2026-10-13', '2026-10-13']);
            assert.deepStrictEqual(rangeFor(2, few, '2026-10-13'), ['2026-10-12', '2026-10-12']);
            assert.deepStrictEqual(rangeFor(7, few, '2026-10-13'), ['2026-10-07', null]);
            assert.deepStrictEqual(rangeFor(1, few, '2026-10-14'), ['2026-10-14', '2026-10-14']);
            assert.deepStrictEqual(rangeFor(0, few, '2026-10-13'), [null, null]);
            const many = Array.from({ length: 200 }, (_, i) => isoDate(addDays(toDate('2026-01-01'), i)));
            assert.deepStrictEqual(rangeFor(0, many, '2026-10-13'), [many[20], null]);
            assert.deepStrictEqual(rangeFor(365, many, '2026-10-13'), ['2026-04-17', null]);
        """, tz="Asia/Ho_Chi_Minh")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_range_label_counts_calendar_days(self):
        """'Last 7 days' is 7 days even when only 2 of them have usage."""
        result = run_node(DATE_HELPERS_JS + """
            const assert = require('assert');
            const data = ['2026-09-01', '2026-10-08', '2026-10-12'];
            assert.strictEqual(rangeLabel('2026-10-07', null, data, '2026-10-13'), ' (7 days)');
            assert.strictEqual(rangeLabel('2026-10-13', '2026-10-13', data, '2026-10-13'), ' (1 day)');
            assert.strictEqual(rangeLabel(null, null, data, '2026-10-13'), ' (all time)');
            assert.strictEqual(rangeLabel('2026-03-01', '2026-03-31', data, '2026-10-13'), ' (31 days)');
            assert.strictEqual(rangeLabel(null, '2026-09-03', data, '2026-10-13'), ' (3 days)');
            assert.strictEqual(plural(1, 'day'), '1 day');
            assert.strictEqual(plural(0, 'day'), '0 days');
            assert.strictEqual(plural(2, 'day'), '2 days');
        """, tz="America/Los_Angeles")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_escape_html(self):
        result = run_node(DATE_HELPERS_JS + PROJECT_HELPERS_JS + """
            require('assert').strictEqual(escapeHtml(`a&b<c>"d'`), 'a&amp;b&lt;c&gt;&quot;d&#39;');
        """)
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

    def test_priced_percent_never_rounds_up_to_100(self):
        """996 of 1,000 priced is 99%: '100%' would claim the cost covers every request."""
        result = run_node(PROJECT_HELPERS_JS + """
            const assert = require('assert');
            assert.strictEqual(pricedPercent({ requests: 1000, unpricedRequests: 4 }), 99);
            assert.strictEqual(pricedPercent({ requests: 3, unpricedRequests: 1 }), 66);
            assert.strictEqual(pricedPercent({ requests: 1000, unpricedRequests: 999 }), 0);
        """)
        self.assertEqual(result.returncode, 0, result.stderr)


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

    def test_no_utc_date_formatting(self):
        """toISOString formats in UTC; east of UTC it names yesterday. (flatpickr's own copy is never called.)"""
        with mock.patch.object(dashboard, "_load_asset", return_value=""):
            self.assertNotIn("toISOString", generate_html(MINIMAL_REPORT))

    def test_today_is_read_when_a_button_is_clicked(self):
        self.assertNotIn("todayStr", self.html)
        self.assertIn("rangeFor(days, allDates, localToday())", self.html)

    def test_project_names_are_escaped_in_the_table(self):
        self.assertIn("escapeHtml(p.name)", self.html)

    def test_a_project_name_cannot_close_the_script(self):
        name = "/srv/a<b</script><script>alert(1)</script>"
        report = dict(MINIMAL_REPORT, by_date_project={"2026-10-13": {name: {"requests": 1}}})
        html = generate_html(report)
        self.assertEqual(html.count("</script>"), self.html.count("</script>"))
        self.assertEqual(list(embedded_data(html)["by_date_project"]["2026-10-13"]), [name])

    def test_payload_carries_only_what_the_page_reads(self):
        """Display strings and the all-time tables are never read by the page; they only make it bigger."""
        day = {"input_tokens": 1, "input_tokens_display": "1", "requests": 1, "requests_display": "1"}
        report = dict(MINIMAL_REPORT, summary={"requests": 1}, by_project={"/a": day},
                      by_date={"2026-10-13": {"claude-opus-5-5": day}},
                      by_date_project={"2026-10-13": {"/a": day}})
        data = embedded_data(generate_html(report))
        self.assertEqual(set(data), {"generated_at", "by_date", "by_date_project"})
        self.assertNotIn("_display", json.dumps(data))
        self.assertEqual(data["by_date"]["2026-10-13"]["claude-opus-5-5"], {"input_tokens": 1, "requests": 1})

    def test_a_project_named_like_a_display_key_is_kept(self):
        report = dict(MINIMAL_REPORT, by_date_project={"2026-10-13": {"/srv/report_display": {"requests": 1}}})
        self.assertEqual(list(embedded_data(generate_html(report))["by_date_project"]["2026-10-13"]),
                         ["/srv/report_display"])

    def test_current_models_have_colors(self):
        for model in ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-haiku-5-5",
                      "claude-fable-5-1"):
            self.assertIn(f"'{model}':", self.html)


if __name__ == "__main__":
    unittest.main()
