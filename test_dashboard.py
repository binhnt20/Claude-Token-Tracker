#!/usr/bin/env python3
"""Tests for the dashboard page. Run: python3 -m unittest test_dashboard -v

The JS helpers run under node when it is installed; the rest checks the generated HTML.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import unittest
from unittest import mock

import dashboard
from dashboard import (DATE_HELPERS_JS, FALLBACK_COLORS, FORMAT_HELPERS_JS, MODEL_COLORS, PROJECT_HELPERS_JS,
                       generate_html)

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


def delta_e(hex_a: str, hex_b: str) -> float:
    """CIE76 distance between two sRGB colours; about 2 is barely visible, 20 is clearly a different colour."""
    def lab(hex_color):
        rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)
        xyz = ((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, 0.2126 * r + 0.7152 * g + 0.0722 * b,
               (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883)
        fx, fy, fz = (t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in xyz)
        return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)
    return sum((p - q) ** 2 for p, q in zip(lab(hex_a), lab(hex_b))) ** 0.5


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
            assert.deepStrictEqual(rangeFor(7, few, '2026-10-13'), ['2026-10-07', '2026-10-13']);
            assert.deepStrictEqual(rangeFor(30, few, '2026-10-13'), ['2026-09-14', '2026-10-13']);
            assert.deepStrictEqual(rangeFor(1, few, '2026-10-14'), ['2026-10-14', '2026-10-14']);
            assert.deepStrictEqual(rangeFor(0, few, '2026-10-13'), [null, null]);
            assert.deepStrictEqual(rangeFor(365, few, '2026-10-13'), ['2025-10-14', '2026-10-13']);
        """, tz="Asia/Ho_Chi_Minh")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_time_is_every_day_with_data(self):
        """'All time' was capped at the last 180 days with data, under a label that said all time (UBU-9)."""
        result = run_node(DATE_HELPERS_JS + """
            const assert = require('assert');
            const many = Array.from({ length: 200 }, (_, i) => isoDate(addDays(toDate('2026-03-25'), i)));
            const [from, to] = rangeFor(0, many, '2026-10-13');
            assert.deepStrictEqual(datesInRange(many, from, to), many);
        """, tz="Asia/Ho_Chi_Minh")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_last_n_days_end_today(self):
        """A transcript stamped in the future (a wrong clock) is not part of 'Last 7 days' (WIN-3)."""
        result = run_node(DATE_HELPERS_JS + """
            const assert = require('assert');
            const data = ['2026-10-06', '2026-10-07', '2026-10-13', '2026-10-22'];
            const [from, to] = rangeFor(7, data, '2026-10-13');
            assert.deepStrictEqual(datesInRange(data, from, to), ['2026-10-07', '2026-10-13']);
            assert.deepStrictEqual(datesInRange(data, ...rangeFor(30, data, '2026-10-13')),
                                   ['2026-10-06', '2026-10-07', '2026-10-13']);
            assert.deepStrictEqual(datesInRange(data, ...rangeFor(0, data, '2026-10-13')), data);
        """, tz="Asia/Ho_Chi_Minh")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_calendar_days_fill_the_gaps(self):
        """The daily chart drew only days with usage, so 09-30 sat next to 10-03 (UBU-12)."""
        result = run_node(DATE_HELPERS_JS + """
            const assert = require('assert');
            assert.deepStrictEqual(calendarDays('2026-09-29', '2026-10-02'),
                                   ['2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02']);
            assert.deepStrictEqual(calendarDays('2026-10-13', '2026-10-13'), ['2026-10-13']);
            assert.deepStrictEqual(calendarDays('2026-10-14', '2026-10-13'), []);
            // The days a DST change shortens or lengthens are still one day each.
            assert.deepStrictEqual(calendarDays('2026-03-07', '2026-03-09'), ['2026-03-07', '2026-03-08', '2026-03-09']);
            assert.deepStrictEqual(calendarDays('2026-10-31', '2026-11-02'), ['2026-10-31', '2026-11-01', '2026-11-02']);
        """, tz="America/Los_Angeles")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chart_days_cover_the_whole_range(self):
        result = run_node(DATE_HELPERS_JS + """
            const assert = require('assert');
            const data = ['2026-09-30', '2026-10-03'];
            assert.deepStrictEqual(chartDays(null, null, data), ['2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03']);
            assert.strictEqual(chartDays('2026-10-07', '2026-10-13', data).length, 7);
            assert.deepStrictEqual(chartDays('2026-10-13', '2026-10-13', []), ['2026-10-13']);
            assert.deepStrictEqual(chartDays(null, null, []), []);
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

    def test_money_format(self):
        """A zero cost read '$0.0000' (UBU-12); small non-zero costs keep their digits."""
        result = run_node(DATE_HELPERS_JS + FORMAT_HELPERS_JS + """
            const assert = require('assert');
            assert.strictEqual(fmtUSD(0), '$0.00');
            assert.strictEqual(fmtUSD(0.0042), '$0.0042');
            assert.strictEqual(fmtUSD(0.5), '$0.50');
            assert.strictEqual(fmtUSD(211.6139), '$211.61');
            assert.strictEqual(fmtUSD(17257.75), '$17.3K');
            assert.strictEqual(fmtVND(0), '0 VND');
        """)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unpriced_note_counts_in_words(self):
        """'Cost excludes 1 requests across 1 model(s)' read as a template, not a sentence."""
        result = run_node(DATE_HELPERS_JS + FORMAT_HELPERS_JS + """
            const assert = require('assert');
            assert.strictEqual(unpricedNote({ unpricedRequests: 0, unpricedModels: new Set(), unpricedTokens: 0 }), '');
            assert.strictEqual(unpricedNote({ unpricedRequests: 1, unpricedModels: new Set(['m']), unpricedTokens: 900 }),
                'Cost excludes 1 request from 1 model with no price table (900 tokens, counted above).');
            assert.strictEqual(
                unpricedNote({ unpricedRequests: 1234, unpricedModels: new Set(['m', 'n']), unpricedTokens: 5600 }),
                'Cost excludes 1,234 requests from 2 models with no price table (5.6K tokens, counted above).');
        """)
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
            // 58 / 100 * 100 is 57.99999999999999 in floating point.
            assert.strictEqual(pricedPercent({ requests: 100, unpricedRequests: 42 }), 58);
            assert.strictEqual(pricedPercent({ requests: 100, unpricedRequests: 71 }), 29);
        """)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestGeneratedHtml(unittest.TestCase):
    def setUp(self):
        self.html = generate_html(MINIMAL_REPORT)

    def test_html_builds_without_assets(self):
        self.assertIn('<canvas id="dailyChart">', self.html)

    def test_helpers_are_inlined(self):
        self.assertIn(DATE_HELPERS_JS, self.html)
        self.assertIn(FORMAT_HELPERS_JS, self.html)
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

    def test_footer_names_each_folder_read_with_its_calls(self):
        sources = [{"folder": "/home/me/.claude/projects", "requests": 17799},
                   {"folder": "/mnt/c/Users/me/<x>/.claude/projects", "requests": 88926}]
        html = generate_html(dict(MINIMAL_REPORT, sources=sources))
        footer = html[html.index("<footer>"):html.index("</footer>")]
        self.assertIn("/home/me/.claude/projects (17,799 calls)", footer)
        self.assertIn("/mnt/c/Users/me/&lt;x&gt;/.claude/projects (88,926 calls)", footer)

    def test_footer_without_sources_is_unchanged(self):
        self.assertIn("<footer>Generated by Claude Code Token Tracker</footer>", self.html)

    def test_current_models_have_colors(self):
        for model in ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-haiku-5-5",
                      "claude-fable-5-1"):
            self.assertIn(model, MODEL_COLORS)
        self.assertIn(f"const modelColors = {json.dumps(MODEL_COLORS)};", self.html)
        self.assertIn(f"const fallbackColors = {json.dumps(FALLBACK_COLORS)};", self.html)

    def test_no_two_chart_colors_look_alike(self):
        """sonnet-5 and sonnet-5-5 were 10 apart in CIE Lab, too close to tell apart in a stacked bar (UBU-12)."""
        colors = {**MODEL_COLORS, **{f"fallback-{i}": c for i, c in enumerate(FALLBACK_COLORS)}}
        for (a, ca), (b, cb) in itertools.combinations(colors.items(), 2):
            with self.subTest(a=a, b=b):
                self.assertGreaterEqual(delta_e(ca, cb), 20)

    def test_custom_button_clears_a_stale_error(self):
        """'Start must be before end' stayed next to a valid range after Last 7 days, then Custom (UBU-8)."""
        handler = re.search(r"btnCustom\.addEventListener\('click', \(\) => \{(.*?)\n\}\);", self.html, re.S)
        self.assertIn("clearRangeHint();", handler.group(1))


class TestLimitsBox(unittest.TestCase):
    """Claude Code's share of the plan limits, rendered in Python from report["limits"]."""

    WINDOW = {"kind": "five_hour", "resets_at": 1_760_018_000, "start": 1_760_000_000, "account_pct": 38.0,
              "claude_code_pct": 30.24, "measured_pct": 29.0, "estimated_pct": 1.24,
              "as_of": "2025-10-09T13:43:20.000Z", "samples": 12, "current": True}

    def limits(self, **extra):
        return {"windows": [self.WINDOW], "current": {"five_hour": self.WINDOW, "seven_day": None}, "samples": 12,
                "enable": "python3 statusline.py --install", "logs": ["/home/u/<x>/limits.jsonl"], **extra}

    def test_current_window(self):
        html = dashboard.limits_html({"limits": self.limits()})
        self.assertIn('<div class="limit-value">30.2%</div>', html)
        self.assertIn("of the 5-hour limit (account total 38.0%)", html)
        self.assertNotIn("Claude Code used 30.2%", html)
        self.assertIn("estimate", html)
        self.assertIn("No sample in the current weekly window yet.", html)
        self.assertIn("/home/u/&lt;x&gt;/limits.jsonl", html)
        self.assertNotIn("<x>", html)

    def test_recent_windows_table_holds_ten(self):
        windows = [dict(self.WINDOW, resets_at=self.WINDOW["resets_at"] - i * 18_000, current=i == 0)
                   for i in range(14)]
        html = dashboard.limits_html({"limits": self.limits(windows=windows)})
        self.assertEqual(html.count('<tr class="limit-window">'), 10)

    def test_no_samples_says_how_to_start(self):
        html = dashboard.limits_html({"limits": {"windows": [], "current": {"five_hour": None, "seven_day": None},
                                                 "samples": 0, "enable": "python statusline.py --install",
                                                 "logs": []}})
        self.assertIn("python statusline.py --install", html)

    def test_no_limits_key_renders_nothing(self):
        self.assertEqual(dashboard.limits_html(MINIMAL_REPORT), "")

    def test_page_carries_the_box(self):
        with mock.patch.object(dashboard, "_load_asset", return_value=""):
            html = generate_html(dict(MINIMAL_REPORT, limits=self.limits()))
        self.assertIn('class="limits-box"', html)


if __name__ == "__main__":
    unittest.main()
