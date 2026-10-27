#!/usr/bin/env python3
"""Tests for Claude Code's share of the plan limits. Run: python3 -m unittest test_limits -v"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import limits

START = 1_760_000_000  # a five-hour window opens here (2025-10-09T08:53:20Z)
R5 = START + 5 * 3600
R7 = START + 3 * 86400  # the weekly window opened four days earlier


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sample(offset, five=None, seven=None, session="s1", resets5=R5, resets7=R7):
    """A row as statusline.py writes it, `offset` seconds after START."""
    row = {"t": iso(START + offset), "session_id": session}
    if five is not None:
        row["five_hour"] = {"pct": float(five), "resets_at": resets5}
    if seven is not None:
        row["seven_day"] = {"pct": float(seven), "resets_at": resets7}
    return row


def call(offset, cost=1.0, session="s1", source=None):
    """A transcript record: one Claude Code API call `offset` seconds after START."""
    record = {"timestamp": iso(START + offset), "session_id": session,
              "cost": None if cost is None else {"input": cost, "total": cost}}
    if source is not None:
        record["source"] = str(source)
    return record


def share(rows, calls, now=START + 3600):
    return limits.claude_code_share(limits.parse_samples(rows), calls, now)


def five(result):
    return [w for w in result["windows"] if w["kind"] == "five_hour"]


def weeks(result):
    return [w for w in result["windows"] if w["kind"] == "seven_day"]


class TestShare(unittest.TestCase):
    def test_only_claude_code_is_the_whole_rise(self):
        rows = [sample(60, five=2), sample(120, five=4), sample(180, five=6)]
        (window,) = five(share(rows, [call(55), call(115), call(175)]))
        self.assertEqual(window["account_pct"], 6.0)
        self.assertEqual(window["claude_code_pct"], 6.0)
        self.assertEqual((window["measured_pct"], window["estimated_pct"]), (6.0, 0.0))
        self.assertEqual(window["as_of"], iso(START + 180))

    def test_a_rise_with_no_claude_code_call_is_not_counted(self):
        """Chat on claude.ai in a break raises the account % with no Claude Code call behind it."""
        rows = [sample(60, five=2), sample(1000, five=5), sample(1060, five=6)]
        (window,) = five(share(rows, [call(55), call(1055)]))
        self.assertEqual(window["account_pct"], 6.0)
        self.assertEqual(window["claude_code_pct"], 3.0)

    def test_a_long_gap_counts_by_cost(self):
        """Over a long gap only the part the Claude Code calls explain counts: % per dollar from the short gaps."""
        rows = [sample(60, five=2), sample(3600, five=10)]
        (window,) = five(share(rows, [call(55, cost=1.0), call(3590, cost=1.0)]))
        self.assertEqual(window["measured_pct"], 2.0)
        self.assertEqual(window["estimated_pct"], 2.0)
        self.assertEqual(window["claude_code_pct"], 4.0)

    def test_a_long_gap_never_counts_more_than_its_rise(self):
        rows = [sample(60, five=2), sample(3600, five=3)]
        (window,) = five(share(rows, [call(55, cost=1.0), call(3590, cost=50.0)]))
        self.assertEqual(window["estimated_pct"], 1.0)
        self.assertEqual(window["claude_code_pct"], 3.0)

    def test_a_long_gap_with_no_rate_yet_counts_in_full(self):
        rows = [sample(3600, five=10)]
        (window,) = five(share(rows, [call(30), call(3590)]))
        self.assertEqual((window["measured_pct"], window["estimated_pct"]), (0.0, 10.0))

    def test_a_window_may_open_an_hour_before_its_rounded_reset(self):
        """Claude Code reports resets_at rounded to the hour (13-10: 13:00:00Z, 00:00:00Z)."""
        rows = [sample(60, five=2)]
        self.assertEqual(five(share(rows, [call(-1800)]))[0]["claude_code_pct"], 2.0)
        self.assertEqual(five(share(rows, [call(-3700)]))[0]["claude_code_pct"], 0.0)

    def test_a_stale_sample_is_dropped(self):
        """A status line re-run without a new response repeats an older, lower value."""
        rows = [sample(60, five=5), sample(70, five=3, session="s2"), sample(130, five=6)]
        (window,) = five(share(rows, [call(55), call(125)]))
        self.assertEqual(window["claude_code_pct"], 6.0)

    def test_windows_split_by_reset_time(self):
        rows = [sample(60, five=2), sample(6 * 3600, five=1, resets5=R5 + 6 * 3600 - 60)]
        result = share(rows, [call(55), call(6 * 3600 - 5)], now=START + 6 * 3600 + 10)
        windows = five(result)
        self.assertEqual([w["resets_at"] for w in windows], [R5 + 6 * 3600 - 60, R5])
        self.assertEqual([w["current"] for w in windows], [True, False])
        self.assertEqual(result["current"]["five_hour"]["resets_at"], R5 + 6 * 3600 - 60)

    def test_reset_times_a_few_seconds_apart_are_one_window(self):
        rows = [sample(60, five=2, resets5=R5), sample(120, five=4, resets5=R5 + 1)]
        self.assertEqual(len(five(share(rows, [call(55), call(115)]))), 1)

    def test_weekly_is_its_own_window(self):
        rows = [sample(60, five=2, seven=40), sample(120, five=4, seven=40.5)]
        result = share(rows, [call(55), call(115)])
        weekly = result["current"]["seven_day"]
        self.assertEqual(weekly["account_pct"], 40.5)
        # The weekly window opened days ago with no samples since, so only the last short gap is measured.
        self.assertEqual(weekly["measured_pct"], 0.5)
        self.assertLessEqual(weekly["claude_code_pct"], weekly["account_pct"])

    def test_a_past_window_is_not_current(self):
        result = share([sample(60, five=2)], [call(55)], now=R5 + 1)
        self.assertIsNone(result["current"]["five_hour"])
        self.assertFalse(five(result)[0]["current"])

    def test_calls_of_another_account_are_left_out(self):
        """Two accounts on one computer: each has its own reset time, and a session belongs to one of them."""
        other_reset = R5 - 3600
        rows = [sample(60, five=2, session="a"), sample(70, five=20, session="b", resets5=other_reset),
                sample(300, five=4, session="a")]
        windows = {w["resets_at"]: w for w in five(share(rows, [call(55, session="a"), call(65, session="b"),
                                                               call(290, session="b")]))}
        self.assertEqual(windows[R5]["claude_code_pct"], 2.0)
        unknown = five(share(rows, [call(55, session="a"), call(290, session="no-bridge")]))
        self.assertEqual({w["resets_at"]: w["claude_code_pct"] for w in unknown}[R5], 4.0)

    def test_an_account_switch_with_the_same_reset_time_starts_a_new_window(self):
        """15-10: both accounts' weekly windows reset Sat 00:00Z, so the old account's 99 % stayed on after the switch."""
        rows = [sample(60, seven=98), sample(120, seven=99, session="s2"),
                sample(600, seven=0), sample(900, seven=1, session="s2"), sample(1200, seven=2)]
        result = share(rows, [call(55), call(115, session="s2"), call(595), call(895, session="s2"), call(1195)])
        weekly = weeks(result)
        self.assertEqual([w["account_pct"] for w in weekly], [2.0, 99.0])
        self.assertEqual([w["current"] for w in weekly], [True, False])
        # The new account counts from its first reading: the calls before it went to the old account.
        self.assertEqual(result["current"]["seven_day"]["claude_code_pct"], 2.0)

    def test_readings_far_below_but_not_three_in_a_row_are_stale(self):
        """14-10: idle sessions re-ran their status line with 67 % while the account stood at 77 %."""
        rows = [sample(60, seven=77), sample(70, seven=67, session="idle"), sample(80, seven=66, session="idle2"),
                sample(130, seven=78), sample(140, seven=60, session="idle3")]
        self.assertEqual([w["account_pct"] for w in weeks(share(rows, [call(55), call(125)]))], [78.0])

    def test_after_a_switch_an_idle_session_of_the_old_account_is_left_out(self):
        """An idle session repeats its own last reading, here the old account's 98 %."""
        rows = [sample(60, seven=98, session="idle"), sample(120, seven=99),
                sample(600, seven=0), sample(900, seven=1), sample(1200, seven=2),
                sample(1300, seven=98, session="idle"), sample(1500, seven=3)]
        result = share(rows, [call(55, session="idle"), call(115), call(595), call(895), call(1195), call(1495)])
        current = result["current"]["seven_day"]
        self.assertEqual((current["account_pct"], current["claude_code_pct"]), (3.0, 3.0))

    def test_unpriced_calls_still_mark_a_gap(self):
        rows = [sample(60, five=2)]
        self.assertEqual(five(share(rows, [call(55, cost=None)]))[0]["claude_code_pct"], 2.0)

    def test_no_samples(self):
        result = share([], [call(55)])
        self.assertEqual(result["windows"], [])
        self.assertEqual(result["current"], {"five_hour": None, "seven_day": None})
        self.assertEqual(result["samples"], 0)


class TestReadSamples(unittest.TestCase):
    def test_merges_files_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp, "a.jsonl"), Path(tmp, "b.jsonl")
            a.write_text(json.dumps(sample(120, five=4)) + "\nnot json\n[1]\n", encoding="utf-8")
            b.write_text(json.dumps(sample(60, five=2)) + "\n" + '{"t": "bad"}\n', encoding="utf-8")
            rows = limits.read_samples([a, b, Path(tmp, "missing.jsonl")])
        self.assertEqual([r["five_hour"]["pct"] for r in rows], [2.0, 4.0])


class TestLogPaths(unittest.TestCase):
    def test_both_sides(self):
        home = Path("/home/u")
        win = Path("/mnt/c/Users/u")
        self.assertEqual(limits.log_paths({}, "linux", home, [win]),
                         [home / ".local/share/claude-token-tracker/limits.jsonl",
                          win / "AppData/Local/claude-token-tracker/limits.jsonl"])
        self.assertEqual(limits.log_paths({"LOCALAPPDATA": "/lad"}, "win32", win, [home]),
                         [Path("/lad/claude-token-tracker/limits.jsonl"),
                          home / ".local/share/claude-token-tracker/limits.jsonl"])
        self.assertEqual(limits.log_paths({}, "darwin", home, []),
                         [home / "Library/Application Support/claude-token-tracker/limits.jsonl"])


class TestDescribe(unittest.TestCase):
    TZ = timezone(timedelta(hours=7))

    def test_current_window(self):
        window = {"kind": "five_hour", "resets_at": R5, "claude_code_pct": 30.24, "account_pct": 38.0,
                  "as_of": iso(R5 - 600), "estimated_pct": 1.0, "current": True}
        text = limits.describe(window, tz=self.TZ)
        self.assertEqual(text, "Claude Code used 30.2% of the 5-hour limit (account total 38.0%) · "
                               "resets 20:53 · as of 20:43")

    def test_without_the_lead(self):
        window = {"kind": "five_hour", "resets_at": R5, "claude_code_pct": 30.24, "account_pct": 38.0,
                  "as_of": iso(R5 - 600), "estimated_pct": 0.0, "current": True}
        self.assertTrue(limits.describe(window, tz=self.TZ, lead=False).startswith("of the 5-hour limit (account"))

    def test_weekly_names_the_day(self):
        window = {"kind": "seven_day", "resets_at": R7, "claude_code_pct": 12.0, "account_pct": 41.0,
                  "as_of": iso(START), "estimated_pct": 0.0, "current": True}
        self.assertIn("of the weekly limit", limits.describe(window, tz=self.TZ))
        self.assertIn("resets Sun 15:53", limits.describe(window, tz=self.TZ))


class TestReport(unittest.TestCase):
    def test_reads_this_sides_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp, "claude-token-tracker", "limits.jsonl")
            log.parent.mkdir()
            log.write_text(json.dumps(sample(60, five=2, seven=1)) + "\n", encoding="utf-8")
            result = limits.report([call(55)], env={"XDG_DATA_HOME": tmp}, home=Path(tmp, "home"),
                                   platform="linux", other_homes=[], now=START + 120)
        self.assertEqual(result["logs"], [str(log)])
        self.assertEqual(result["current"]["five_hour"]["claude_code_pct"], 2.0)
        self.assertEqual(result["enable"], "python3 statusline.py --install")

    def test_windows_names_python(self):
        result = limits.report([], env={"LOCALAPPDATA": "/nowhere"}, home=Path("/nowhere"), platform="win32",
                               other_homes=[], now=START)
        self.assertEqual(result["enable"], "python statusline.py --install")


DAY = 86400


class TestStaleLogs(unittest.TestCase):
    """A log that stops while Claude Code keeps running on its side: the bridge was removed (14-10: Orca rewrote
    the status line, and 12 days of Windows samples were lost without a word)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home, self.win = self.base / "home", self.base / "c" / "Users" / "me"
        self.mine = self.home / ".claude" / "projects"
        self.theirs = self.win / ".claude" / "projects"

    def log(self, path, *rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    def report(self, calls, other_homes=()):
        return limits.report(calls, env={}, home=self.home, platform="linux", other_homes=list(other_homes),
                             now=START + 3 * DAY)

    def this_log(self):
        return self.home / ".local" / "share" / "claude-token-tracker" / "limits.jsonl"

    def windows_log(self):
        return self.win / "AppData" / "Local" / "claude-token-tracker" / "limits.jsonl"

    def test_calls_a_day_after_the_last_sample_make_the_log_stale(self):
        log = self.log(self.this_log(), sample(60, five=2, seven=1))
        result = self.report([call(55, source=self.mine), call(60 + 2 * DAY, source=self.mine)])
        self.assertEqual(result["stale_logs"], [{"log": str(log), "last_sample": iso(START + 60),
                                                 "last_call": iso(START + 60 + 2 * DAY),
                                                 "enable": "python3 statusline.py --install"}])

    def test_calls_within_a_day_keep_the_log_fresh(self):
        self.log(self.this_log(), sample(60, five=2, seven=1))
        result = self.report([call(55, source=self.mine), call(60 + DAY - 60, source=self.mine)])
        self.assertEqual(result["stale_logs"], [])

    def test_no_log_is_not_stale(self):
        """Never installed on that side is the user's choice, already explained by the 'turn them on' line."""
        result = self.report([call(60 + 2 * DAY, source=self.mine)])
        self.assertEqual(result["stale_logs"], [])

    def test_each_log_is_judged_on_its_own_sides_calls(self):
        self.log(self.this_log(), sample(60 + 2 * DAY, five=2, seven=1))
        theirs = self.log(self.windows_log(), sample(60, five=1, seven=1))
        only_mine = self.report([call(60 + 2 * DAY, source=self.mine)], other_homes=[self.win])
        self.assertEqual(only_mine["stale_logs"], [])
        both = self.report([call(60 + 2 * DAY, source=self.mine), call(60 + 2 * DAY, source=self.theirs)],
                           other_homes=[self.win])
        self.assertEqual([(s["log"], s["enable"]) for s in both["stale_logs"]],
                         [(str(theirs), "python statusline.py --install")])

    def test_stale_lines_name_the_log_and_the_command(self):
        log = self.log(self.this_log(), sample(60, five=2, seven=1))
        result = self.report([call(60 + 2 * DAY, source=self.mine)])
        (line,) = limits.stale_lines(result, tz=timezone.utc)
        self.assertIn(str(log), line)
        self.assertIn("python3 statusline.py --install", line)
        self.assertIn("since " + datetime.fromtimestamp(START + 60, timezone.utc).strftime("%d %b %H:%M"), line)
        self.assertEqual(limits.stale_lines({"samples": 0}), [])


class TestSummaryLines(unittest.TestCase):
    TZ = timezone(timedelta(hours=7))

    def test_current_windows(self):
        result = share([sample(60, five=2, seven=40)], [call(55)], now=START + 120)
        result["enable"] = "python3 statusline.py --install"
        lines = limits.summary_lines(result, tz=self.TZ)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("Claude Code used 2.0% of the 5-hour limit"))
        self.assertIn("of the weekly limit", lines[1])

    def test_a_kind_without_a_current_window(self):
        result = share([sample(60, five=2)], [call(55)], now=START + 120)
        result["enable"] = "python3 statusline.py --install"
        self.assertEqual(limits.summary_lines(result, tz=self.TZ)[1], "No sample in the current weekly window yet.")

    def test_no_samples_says_how_to_start(self):
        result = share([], [])
        result["enable"] = "python3 statusline.py --install"
        (line,) = limits.summary_lines(result)
        self.assertIn("python3 statusline.py --install", line)


if __name__ == "__main__":
    unittest.main()
