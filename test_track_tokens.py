#!/usr/bin/env python3
"""Tests for transcript parsing and de-duplication. Run: python3 -m unittest test_track_tokens -v"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

import limits
import pricing
import track_tokens
from sources import (iter_transcripts, other_side_homes, running_wsl_distros, transcript_candidates, wsl_exe,
                     transcript_roots, windows_homes_on_path, wsl_distro_homes)
from track_tokens import (
    TranscriptsNotFound,
    build_report,
    collect_all_entries,
    dedupe,
    encode_project_dir,
    entry_date,
    parse_transcript,
    project_display_name,
    report_path,
    utc_date,
)


def assistant_line(*, msg_id="msg_1", request_id="req_1", session="s1", uuid=None, output=10, input_tokens=5,
                   cache_read=1_000, model="claude-opus-5-5", timestamp="2026-10-13T10:00:00Z", cwd="/work/repo",
                   **usage_extra):
    """One assistant JSONL line as Claude Code writes it; None drops an id field."""
    message = {"model": model, "usage": {
        "input_tokens": input_tokens, "output_tokens": output, "cache_read_input_tokens": cache_read,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}, **usage_extra}}
    if msg_id is not None:
        message["id"] = msg_id
    entry = {"type": "assistant", "timestamp": timestamp, "sessionId": session, "cwd": cwd,
             "isSidechain": False, "message": message}
    if request_id is not None:
        entry["requestId"] = request_id
    if uuid is not None:
        entry["uuid"] = uuid
    return entry


class TranscriptCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.multiple(pricing, _MULTIPLIER=1.0, _OVERRIDES={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, lines, name="t.jsonl", folder=None):
        directory = Path(self.tmp.name) / folder if folder else Path(self.tmp.name)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        return path


class TestDedupe(TranscriptCase):
    def test_streamed_blocks_count_once(self):
        """Claude Code writes one line per content block; the first carries a partial output count."""
        path = self.write([assistant_line(output=8), assistant_line(output=471)])
        records = dedupe(parse_transcript(path))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["output_tokens"], 471)
        self.assertEqual(records[0]["cache_read_input_tokens"], 1_000)

    def test_max_output_wins_whatever_the_order(self):
        path = self.write([assistant_line(output=471), assistant_line(output=8)])
        self.assertEqual(dedupe(parse_transcript(path))[0]["output_tokens"], 471)

    def test_tie_keeps_the_later_line(self):
        path = self.write([assistant_line(output=9, timestamp="2026-10-13T10:00:00Z"),
                           assistant_line(output=9, timestamp="2026-10-13T10:00:05Z")])
        self.assertEqual(dedupe(parse_transcript(path))[0]["timestamp"], "2026-10-13T10:00:05Z")

    def test_first_appearance_order_is_kept(self):
        path = self.write([assistant_line(request_id="a"), assistant_line(request_id="b"),
                           assistant_line(request_id="a", output=99)])
        records = dedupe(parse_transcript(path))
        self.assertEqual([r["key"] for r in records], ["msg_1:a", "msg_1:b"])
        self.assertEqual(records[0]["output_tokens"], 99)

    def test_dedupe_across_files(self):
        """A resumed session copies earlier lines into a new file."""
        first = self.write([assistant_line(output=471)], name="a.jsonl")
        second = self.write([assistant_line(output=471)], name="b.jsonl")
        self.assertEqual(len(dedupe(parse_transcript(first) + parse_transcript(second))), 1)

    def test_distinct_requests_kept(self):
        path = self.write([assistant_line(request_id="req_1"), assistant_line(request_id="req_2")])
        self.assertEqual(len(dedupe(parse_transcript(path))), 2)

    def test_keyless_lines_not_merged(self):
        path = self.write([assistant_line(msg_id=None, request_id=None), assistant_line(msg_id=None, request_id=None)])
        records = dedupe(parse_transcript(path))
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["key"].startswith("line:") for r in records))

    def test_key_falls_back_to_session_then_uuid(self):
        path = self.write([assistant_line(request_id=None, session="s9"),
                           assistant_line(msg_id=None, request_id=None, uuid="u-1")])
        self.assertEqual([r["key"] for r in parse_transcript(path)], ["msg_1:s9", "uuid:u-1"])


class TestRecordFields(TranscriptCase):
    def test_record_carries_call_context(self):
        record = parse_transcript(self.write([assistant_line()]))[0]
        self.assertEqual(record["cwd"], "/work/repo")
        self.assertFalse(record["is_sidechain"])
        self.assertEqual(record["session_id"], "s1")
        self.assertEqual(record["price_model"], "claude-opus-5-5")
        self.assertEqual(record["speed"], None)
        self.assertEqual(record["web_search_requests"], 0)

    def test_fast_is_priced_and_labelled(self):
        record = parse_transcript(self.write([assistant_line(input_tokens=0, cache_read=0, output=1_000_000,
                                                             speed="fast")]))[0]
        self.assertEqual(record["model"], "claude-opus-5-5 (fast)")
        self.assertEqual(record["price_model"], "claude-opus-5-5")
        self.assertAlmostEqual(record["cost"]["output"], 40)

    def test_us_geo_priced(self):
        record = parse_transcript(self.write([assistant_line(input_tokens=1_000_000, cache_read=0, output=0,
                                                             inference_geo="us")]))[0]
        self.assertEqual(record["inference_geo"], "us")
        self.assertAlmostEqual(record["cost"]["input"], 4.4)

    def test_web_search_counted(self):
        record = parse_transcript(self.write([assistant_line(
            input_tokens=0, cache_read=0, output=0, server_tool_use={"web_search_requests": 3})]))[0]
        self.assertEqual(record["web_search_requests"], 3)
        self.assertAlmostEqual(record["cost"]["web_search"], 0.03)

    @unittest.skipUnless(hasattr(time, "tzset"), "time.tzset is POSIX only")
    def test_price_window_test_restores_the_zone(self):
        """A test that changes TZ must leave the process zone as it found it, or later tests depend on order."""
        with mock.patch.dict(os.environ, {"TZ": "America/New_York"}):
            time.tzset()
            before = time.strftime("%z", time.localtime(1_800_000_000))
            result = unittest.TestResult()
            type(self)("test_price_window_uses_utc_date").run(result)
            after = time.strftime("%z", time.localtime(1_800_000_000))
        time.tzset()
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertEqual(after, before)

    @unittest.skipUnless(hasattr(time, "tzset"), "time.tzset is POSIX only")
    def test_price_window_uses_utc_date(self):
        """20:00 UTC on 10-06 is 10-07 in Ho Chi Minh City; the 10-06 price still applies."""
        patcher = mock.patch.dict(os.environ, {"TZ": "Asia/Ho_Chi_Minh"})
        patcher.start()
        # Cleanups run last-in first-out: restore TZ, then re-read it.
        self.addCleanup(time.tzset)
        self.addCleanup(patcher.stop)
        time.tzset()
        record = parse_transcript(self.write([assistant_line(
            model="claude-sonnet-5-5", input_tokens=0, output=0, cache_read=1_000_000,
            timestamp="2026-10-06T20:00:00Z")]))[0]
        self.assertAlmostEqual(record["cost"]["cache_read"], 0.20)


class TestLineQuality(TranscriptCase):
    def test_only_lines_that_name_usage_are_decoded(self):
        """Most transcript lines are prompts, tool output and progress; skipping them before json.loads halves a scan."""
        path = self.write([{"type": "user", "message": {"content": "hi"}}, assistant_line(),
                           {"type": "progress", "data": {"usage note": "not a key"}}])
        with mock.patch.object(track_tokens.json, "loads", wraps=json.loads) as loads:
            records = parse_transcript(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(loads.call_count, 1)

    def test_synthetic_lines_are_not_requests(self):
        """Claude Code writes '<synthetic>' lines itself (no API call, 0 tokens); they must not count as requests."""
        path = self.write([assistant_line(request_id="r1"),
                           assistant_line(msg_id="m2", request_id="r2", model="<synthetic>", output=0,
                                          input_tokens=0, cache_read=0)])
        records = parse_transcript(path)
        self.assertEqual([r["price_model"] for r in records], ["claude-opus-5-5"])

    def test_synthetic_leaves_no_unpriced_note(self):
        self.write([assistant_line(request_id="r1"),
                    assistant_line(msg_id="m2", request_id="r2", model="<synthetic>", output=0, input_tokens=0,
                                   cache_read=0)], folder="-work-repo")
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[Path(self.tmp.name)]):
            summary = build_report(*collect_all_entries())["summary"]
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(summary["unpriced_requests"], 0)

    def test_null_token_fields_count_as_zero(self):
        line = assistant_line(output=None, input_tokens=None, cache_read=None)
        line["message"]["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": None,
                                                      "ephemeral_1h_input_tokens": 3}
        self.write([line], folder="-work-repo")
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[Path(self.tmp.name)]):
            summary = build_report(*collect_all_entries())["summary"]
        self.assertEqual(summary["output_tokens"], 0)
        self.assertEqual(summary["cache_creation_1h_tokens"], 3)
        self.assertEqual(summary["unpriced_requests"], 0)

    def test_legacy_null_cache_total_counts_as_zero(self):
        line = assistant_line()
        line["message"]["usage"].pop("cache_creation")
        line["message"]["usage"]["cache_creation_input_tokens"] = None
        (record,) = parse_transcript(self.write([line]))
        self.assertEqual(record["cache_creation_input_tokens"], 0)
        self.assertIsNotNone(record["cost"])

    def test_one_bad_byte_drops_nothing_else(self):
        """An undecodable byte used to abandon the rest of the file."""
        good = [json.dumps(assistant_line(request_id=f"r{i}")).encode() for i in (1, 2, 3)]
        bad_cwd = good[1].replace(b"/work/repo", b"/work/r\xffepo")
        path = Path(self.tmp.name) / "bad.jsonl"
        path.write_bytes(b"\n".join([good[0], bad_cwd, good[2]]) + b"\n")
        records = parse_transcript(path)
        self.assertEqual([r["key"] for r in records], ["msg_1:r1", "msg_1:r2", "msg_1:r3"])


class TestCollectAllEntries(TranscriptCase):
    def test_progress_counts_files_after_listing_them_all(self):
        self.write([assistant_line(msg_id="m1")], name="a.jsonl", folder="-work-a")
        self.write([assistant_line(msg_id="m2")], name="b.jsonl", folder="-work-b")
        seen = []
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[Path(self.tmp.name)]):
            collect_all_entries(progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(0, 2), (1, 2), (2, 2)])

    def test_project_totals_count_each_call_once(self):
        self.write([assistant_line(output=8), assistant_line(output=471)], name="a.jsonl", folder="-work-repo")
        self.write([assistant_line(output=471)], name="b.jsonl", folder="-work-repo")
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[Path(self.tmp.name)]):
            entries, projects = collect_all_entries()
        self.assertEqual(len(entries), 1)
        (stats,) = projects.values()
        self.assertEqual(stats["requests"], 1)
        self.assertEqual(stats["output_tokens"], 471)
        self.assertIn("web_search", stats["cost"])

    def test_report_names_each_folder_read_with_its_calls(self):
        """On a WSL machine the user must see that both sides were counted, and how much each one holds."""
        self.write([assistant_line(msg_id="m1"), assistant_line(msg_id="m2")], folder="linux/projects/-work-repo")
        self.write([assistant_line(msg_id="m3")], folder="windows/projects/C--work-repo")
        roots = [Path(self.tmp.name) / "linux" / "projects", Path(self.tmp.name) / "windows" / "projects"]
        with mock.patch.object(track_tokens, "transcript_roots", return_value=roots):
            entries, projects = collect_all_entries()
        report = build_report(entries, projects, date_filter=None)
        self.assertEqual(report["sources"], [{"folder": str(roots[0]), "requests": 2},
                                             {"folder": str(roots[1]), "requests": 1}])

    def test_not_found_names_every_folder_looked_in(self):
        missing = [Path(self.tmp.name) / "a", Path(self.tmp.name) / "b"]
        env = {"CLAUDE_TOKEN_TRACKER_DIRS": ",".join(str(m) for m in missing)}
        with mock.patch.dict(os.environ, env), self.assertRaises(TranscriptsNotFound) as caught:
            collect_all_entries()
        self.assertEqual(caught.exception.candidates, [m / "projects" for m in missing])
        for folder in missing:
            self.assertIn(str(folder / "projects"), str(caught.exception))

    def test_cli_reports_not_found_on_stderr_and_exits_1(self):
        err = io.StringIO()
        missing = Path("/nowhere/projects")
        with mock.patch.object(track_tokens, "collect_all_entries",
                               side_effect=TranscriptsNotFound([missing])), \
                mock.patch.object(track_tokens.sys, "argv", ["track_tokens.py"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as caught:
            track_tokens.main()
        self.assertEqual(caught.exception.code, 1)
        self.assertIn(str(missing), err.getvalue())


class TestCliArguments(unittest.TestCase):
    """track_tokens.py [today | yesterday | YYYY-MM-DD | -h]: a typo must not print an empty report that exits 0."""

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(track_tokens, "collect_all_entries", return_value=([], {})) as collect, \
                mock.patch.object(track_tokens, "build_report", wraps=build_report) as report, \
                mock.patch.object(track_tokens, "OUTPUT_FILE", Path(tmp.name) / "token_usage.json"), \
                mock.patch.object(track_tokens.sys, "argv", ["track_tokens.py", *args]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                track_tokens.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue(), collect, report

    def test_help_prints_usage_without_scanning(self):
        for flag in ("-h", "--help"):
            code, out, _err, collect, _report = self.run_cli(flag)
            self.assertEqual(code, 0)
            self.assertIn("usage", out.lower())
            self.assertIn("YYYY-MM-DD", out)
            collect.assert_not_called()

    def test_usage_names_the_python_command_of_the_os(self):
        """On Windows `python3` opens the Microsoft Store stub; the python.org installer gives `python` (WIN-2)."""
        self.assertIn("Usage: python track_tokens.py", track_tokens.usage_text("win32"))
        for platform in ("linux", "darwin"):
            self.assertIn("Usage: python3 track_tokens.py", track_tokens.usage_text(platform))
        _code, out, _err, _collect, _report = self.run_cli("--help")
        self.assertEqual(out.strip(), track_tokens.usage_text().strip())

    def test_a_date_that_does_not_exist_exits_2_without_scanning(self):
        for arg in ("2026-13-45", "2026-02-30", "tomorrow", "--today"):
            code, _out, err, collect, _report = self.run_cli(arg)
            self.assertEqual(code, 2, arg)
            self.assertIn(arg, err)
            collect.assert_not_called()

    def test_a_report_file_that_cannot_be_written_is_a_warning(self):
        """A read-only install folder must not cost the user the summary."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        blocker = Path(tmp.name) / "not-a-folder"
        blocker.write_text("", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(track_tokens, "collect_all_entries", return_value=([], {})), \
                mock.patch.object(limits, "report", return_value=limits.claude_code_share([], [], 0)), \
                mock.patch.object(track_tokens, "OUTPUT_FILE", blocker / "token_usage.json"), \
                mock.patch.object(track_tokens.sys, "argv", ["track_tokens.py"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            track_tokens.main()
        self.assertIn("TOTAL:", out.getvalue())
        self.assertNotIn("Report saved to", out.getvalue())
        self.assertIn("could not save", err.getvalue())
        self.assertIn(str(blocker / "token_usage.json"), err.getvalue())

    def test_a_console_that_cannot_encode_the_argument(self):
        """A redirected Windows console is cp1252: echoing a Vietnamese typo back must not raise."""
        raw = io.BytesIO()
        err = io.TextIOWrapper(raw, encoding="cp1252")
        with mock.patch.object(track_tokens.sys, "argv", ["track_tokens.py", "thử"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as exit_:
            track_tokens.main()
        err.flush()
        self.assertEqual(exit_.exception.code, 2)
        self.assertIn(b"'th?'", raw.getvalue())

    def test_a_real_date_filters_on_the_zero_padded_day(self):
        for arg, day in (("2026-10-10", "2026-10-10"), ("2026-1-5", "2026-01-05")):
            code, out, _err, _collect, report = self.run_cli(arg)
            self.assertEqual(code, 0)
            self.assertEqual(report.call_args.args[2], day)
            self.assertIn(f"Filter: {day}", out)


@unittest.skipUnless(hasattr(time, "tzset"), "time.tzset is POSIX only")
class TestLocalDates(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"TZ": "Asia/Ho_Chi_Minh"})
        patcher.start()
        time.tzset()
        self.addCleanup(time.tzset)
        self.addCleanup(patcher.stop)

    def test_entry_date_is_the_local_day(self):
        self.assertEqual(entry_date("2026-10-12T18:30:00Z"), "2026-10-13")

    def test_utc_date_stays_utc(self):
        self.assertEqual(utc_date("2026-10-12T18:30:00Z"), "2026-10-12")

    def test_bad_timestamps(self):
        self.assertIsNone(entry_date("not a time"))
        self.assertIsNone(utc_date(None))


class TestLocalDatesAnyPlatform(unittest.TestCase):
    """The same local-day rules with an explicit zone, so they also run where time.tzset does not exist."""

    def test_east_of_utc(self):
        self.assertEqual(entry_date("2026-10-12T18:30:00Z", tz=timezone(timedelta(hours=7))), "2026-10-13")

    def test_west_of_utc(self):
        self.assertEqual(entry_date("2026-10-13T03:00:00Z", tz=timezone(timedelta(hours=-7))), "2026-10-12")

    def test_offset_in_the_timestamp_is_honoured(self):
        self.assertEqual(entry_date("2026-10-13T01:00:00+09:00", tz=timezone.utc), "2026-10-12")


class TestProjectNames(unittest.TestCase):
    HOME = PurePosixPath("/home/u")  # a POSIX home on every OS, so the cases also run on Windows

    def test_encoding_matches_claude_code(self):
        self.assertEqual(encode_project_dir("/home/u/code/projects-sync/fraud-6"), "-home-u-code-projects-sync-fraud-6")
        self.assertEqual(encode_project_dir("D:\\Work\\repo"), "D--Work-repo")

    def test_matching_cwd_wins_and_home_is_tilde(self):
        cwds = {"/home/u/code/projects-sync/fraud-6", "/home/u/code/projects-sync/fraud-6/backend"}
        self.assertEqual(project_display_name("-home-u-code-projects-sync-fraud-6", cwds, home=self.HOME),
                         "~/code/projects-sync/fraud-6")

    def test_windows_folder_resolves_to_its_cwd(self):
        self.assertEqual(project_display_name("D--Work-repo", {"D:\\Work\\repo"}, home=self.HOME),
                         "D:\\Work\\repo")

    def test_no_matching_cwd_uses_the_shortest(self):
        cwds = {"/srv/app/sub", "/srv/app"}
        self.assertEqual(project_display_name("-srv-renamed", cwds, home=self.HOME), "/srv/app")

    def test_no_cwd_keeps_the_folder_name(self):
        self.assertEqual(project_display_name("-srv-app", set(), home=self.HOME), "-srv-app")

    def test_home_itself_is_tilde(self):
        self.assertEqual(project_display_name("-home-u", {"/home/u"}, home=self.HOME), "~")

    def test_windows_home_matches_whatever_the_case(self):
        """Windows paths ignore case: C:\\Users\\binh and C:\\Users\\Binh are the same folder."""
        name = project_display_name("C--Users-Binh-Code-Repo", {"C:\\Users\\Binh\\Code\\Repo"},
                                    home=PureWindowsPath("c:\\users\\binh"))
        self.assertEqual(name, "~\\Code\\Repo")

    def test_posix_home_stays_case_sensitive(self):
        self.assertEqual(project_display_name("-Home-U-x", {"/Home/U/x"}, home=self.HOME), "/Home/U/x")


class TestReport(TranscriptCase):
    DAY1, DAY2 = "2026-10-10", "2026-10-12"

    def collect(self):
        # Noon UTC lands on the same calendar day in every time zone from UTC-11 to UTC+11.
        self.write([assistant_line(request_id="r1", output=100, cwd="/srv/alpha",
                                   timestamp=f"{self.DAY1}T12:00:00Z")], folder="-srv-alpha")
        self.write([assistant_line(request_id="r2", output=7, cwd="/srv/beta",
                                   timestamp=f"{self.DAY2}T12:00:00Z")], folder="-srv-beta")
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[Path(self.tmp.name)]):
            return collect_all_entries()

    def test_records_carry_the_cwd_project_name(self):
        entries, _ = self.collect()
        self.assertEqual(sorted(e["project"] for e in entries), ["/srv/alpha", "/srv/beta"])

    def test_by_date_project_has_both_days(self):
        report = build_report(*self.collect())
        self.assertEqual(set(report["by_date_project"]), {self.DAY1, self.DAY2})
        self.assertEqual(report["by_date_project"][self.DAY1]["/srv/alpha"]["output_tokens"], 100)
        self.assertEqual(report["by_date_project"][self.DAY2]["/srv/beta"]["output_tokens"], 7)

    def test_projects_follow_the_date_filter(self):
        report = build_report(*self.collect(), date_filter=self.DAY1)
        self.assertEqual(list(report["by_project"]), ["/srv/alpha"])
        self.assertEqual(report["by_project"]["/srv/alpha"]["output_tokens"], 100)
        self.assertEqual(list(report["by_date_project"]), [self.DAY1])

    def test_unfiltered_projects_sum_every_day(self):
        report = build_report(*self.collect())
        self.assertEqual(report["by_project"]["/srv/alpha"]["requests"], 1)
        self.assertEqual(report["by_project"]["/srv/beta"]["requests"], 1)
        self.assertIsNotNone(report["by_project"]["/srv/alpha"]["cost"])


class TestPrintSummary(TranscriptCase):
    def test_recent_dates_end_today_and_name_a_day_after_it(self):
        """Headed TOP DATES, the list held the 7 latest days, led by a day a wrong clock put in the future."""
        self.write([assistant_line(msg_id=day, request_id=day, timestamp=f"{day}T12:00:00Z")
                    for day in ("2026-10-22", "2026-10-13", "2026-10-12")], folder="-work-repo")
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[Path(self.tmp.name)]):
            report = build_report(*collect_all_entries())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            track_tokens.print_summary(report, today="2026-10-13")
        text = out.getvalue()
        self.assertNotIn("TOP DATES", text)
        listed = text.split("RECENT DATES:\n")[1].split("\n\n")[0].splitlines()
        self.assertEqual([line.split(":")[0].strip() for line in listed], ["2026-10-13", "2026-10-12"])
        self.assertIn("1 day after today", text)
        self.assertIn("2026-10-22", text)

    def test_plan_limit_lines(self):
        report = build_report([], {})
        report["limits"] = {"windows": [], "current": {"five_hour": None, "seven_day": None}, "samples": 0,
                            "enable": "python3 statusline.py --install", "logs": []}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            track_tokens.print_summary(report)
        self.assertIn("CLAUDE CODE SHARE OF PLAN LIMITS (estimate)", out.getvalue())
        self.assertIn("python3 statusline.py --install", out.getvalue())

    def test_stale_log_warning_line(self):
        report = build_report([], {})
        report["limits"] = {"windows": [], "current": {"five_hour": None, "seven_day": None}, "samples": 3,
                            "enable": "python3 statusline.py --install", "logs": [],
                            "stale_logs": [{"log": "/mnt/c/u/limits.jsonl", "last_sample": "2026-10-14T23:56:36.783Z",
                                            "last_call": "2026-10-26T14:00:00.000Z",
                                            "enable": "python statusline.py --install"}]}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            track_tokens.print_summary(report)
        self.assertIn("No samples in /mnt/c/u/limits.jsonl since", out.getvalue())
        self.assertIn("Run `python statusline.py --install` there again.", out.getvalue())

    def test_non_ascii_project_on_a_cp1252_stdout(self):
        """A redirected Windows stdout is cp1252; a Vietnamese folder name must not crash the summary."""
        self.write([assistant_line(cwd="D:\\Dự án\\repo")], folder="D--D--n-repo")
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[Path(self.tmp.name)]):
            report = build_report(*collect_all_entries())
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        with contextlib.redirect_stdout(stream):
            track_tokens.print_summary(report)
        stream.flush()
        self.assertIn(b"D:\\D? \xe1n\\repo", raw.getvalue())


class TestSources(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def mkdirs(self, *parts):
        for part in parts:
            (self.base / part).mkdir(parents=True, exist_ok=True)
        return [self.base / part for part in parts]

    def test_config_dir_list(self):
        a, b = self.mkdirs("a/projects", "b/projects")
        env = {"CLAUDE_CONFIG_DIR": f"{self.base / 'a'},{self.base / 'b'}"}
        self.assertEqual(transcript_roots(env=env, home=self.base / "home", other_homes=[]), [a, b])

    def test_config_dir_comes_before_the_defaults(self):
        """Orca and other account switchers set CLAUDE_CONFIG_DIR in every shell: it must not hide ~/.claude."""
        first, legacy = self.mkdirs("a/projects", "home/.claude/projects")
        env = {"CLAUDE_CONFIG_DIR": str(self.base / "a")}
        self.assertEqual(transcript_roots(env=env, home=self.base / "home", other_homes=[]), [first, legacy])

    def test_tracker_dirs_replace_every_default(self):
        (only,) = self.mkdirs("a/projects")
        self.mkdirs("home/.claude/projects", "b/projects")
        env = {"CLAUDE_TOKEN_TRACKER_DIRS": str(self.base / "a"), "CLAUDE_CONFIG_DIR": str(self.base / "b")}
        self.assertEqual(transcript_roots(env=env, home=self.base / "home", other_homes=[]), [only])

    def test_default_reads_both_home_dirs(self):
        legacy, xdg = self.mkdirs("home/.claude/projects", "home/.config/claude/projects")
        self.assertEqual(transcript_roots(env={}, home=self.base / "home", other_homes=[]), [legacy, xdg])

    def test_candidates_are_every_folder_looked_in(self):
        home = self.base / "home"
        self.assertEqual(transcript_candidates(env={}, home=home, other_homes=[]),
                         [home / ".claude" / "projects", home / ".config" / "claude" / "projects"])
        env = {"CLAUDE_CONFIG_DIR": f"{self.base / 'a'}, {self.base / 'b'}"}
        self.assertEqual(transcript_candidates(env=env, home=home, other_homes=[]),
                         [self.base / "a" / "projects", self.base / "b" / "projects",
                          home / ".claude" / "projects", home / ".config" / "claude" / "projects"])
        env = {"CLAUDE_TOKEN_TRACKER_DIRS": f"{self.base / 'a'}, {self.base / 'b'}"}
        self.assertEqual(transcript_candidates(env=env, home=home),
                         [self.base / "a" / "projects", self.base / "b" / "projects"])

    def test_missing_dirs_are_skipped(self):
        (legacy,) = self.mkdirs("home/.claude/projects")
        self.assertEqual(transcript_roots(env={}, home=self.base / "home", other_homes=[]), [legacy])
        self.assertEqual(transcript_roots(env={}, home=self.base / "nobody", other_homes=[]), [])

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs POSIX permissions and a non-root user")
    def test_unreachable_root_is_skipped_with_a_warning(self):
        """A root-owned ~/.config/claude (after `sudo claude`) must not stop the scan of ~/.claude."""
        (legacy,) = self.mkdirs("home/.claude/projects")
        locked = self.base / "home/.config/claude"
        locked.mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            roots = transcript_roots(env={}, home=self.base / "home", other_homes=[])
        self.assertEqual(roots, [legacy])
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)

    def test_symlinked_duplicate_listed_once(self):
        (legacy,) = self.mkdirs("home/.claude/projects")
        (self.base / "home/.config").mkdir()
        try:
            (self.base / "home/.config/claude").symlink_to(self.base / "home/.claude", target_is_directory=True)
        except OSError:
            self.skipTest("cannot create symlinks here")
        self.assertEqual(transcript_roots(env={}, home=self.base / "home", other_homes=[]), [legacy])

    WINDOWS_PATH = "/usr/bin:/mnt/c/Users/me/AppData/Local/Microsoft/WindowsApps:/mnt/c/WINDOWS/system32"

    def test_wsl_also_looks_in_the_windows_user_folder(self):
        """WSL puts the Windows PATH after its own; the WindowsApps entry names the Windows user folder."""
        home, windows = self.base / "home", Path("/mnt/c/Users/me")
        self.assertEqual(transcript_candidates(env={"PATH": self.WINDOWS_PATH}, home=home, platform="linux"),
                         [home / ".claude" / "projects", home / ".config" / "claude" / "projects",
                          windows / ".claude" / "projects", windows / ".config" / "claude" / "projects"])

    def test_windows_user_folders_on_path_listed_once_in_order(self):
        path = ("/mnt/d/Users/Kim/AppData/Local/Microsoft/WindowsApps/:/bin:"
                "/c/Users/me/AppData/Local/Microsoft/WindowsApps:/mnt/d/Users/Kim/AppData/Local/Microsoft/WindowsApps")
        self.assertEqual(windows_homes_on_path(path), [Path("/mnt/d/Users/Kim"), Path("/c/Users/me")])
        self.assertEqual(windows_homes_on_path("/usr/bin:/mnt/c/WINDOWS"), [])
        self.assertEqual(windows_homes_on_path(""), [])

    def test_a_windows_path_means_nothing_off_linux(self):
        self.assertEqual(other_side_homes({"PATH": self.WINDOWS_PATH}, "darwin"), [])

    def test_config_dir_keeps_the_other_side(self):
        home, windows = self.base / "home", Path("/mnt/c/Users/me")
        env = {"CLAUDE_CONFIG_DIR": str(self.base / "a"), "PATH": self.WINDOWS_PATH}
        self.assertEqual(transcript_candidates(env=env, home=home, platform="linux"),
                         [self.base / "a" / "projects", home / ".claude" / "projects",
                          home / ".config" / "claude" / "projects",
                          windows / ".claude" / "projects", windows / ".config" / "claude" / "projects"])

    def test_tracker_dirs_also_replace_the_other_side(self):
        env = {"CLAUDE_TOKEN_TRACKER_DIRS": str(self.base / "a"), "PATH": self.WINDOWS_PATH}
        self.assertEqual(transcript_candidates(env=env, home=self.base / "home", platform="linux"),
                         [self.base / "a" / "projects"])

    def test_wsl_reads_both_sides_that_exist(self):
        linux, windows = self.mkdirs("home/.claude/projects", "c/Users/me/.claude/projects")
        self.assertEqual(transcript_roots(env={}, home=self.base / "home", other_homes=[self.base / "c/Users/me"]),
                         [linux, windows])

    def test_windows_looks_in_the_homes_of_running_distros(self):
        share = self.base / "share"
        self.mkdirs("share/Ubuntu-24.04/home/me", "share/Ubuntu-24.04/home/kim", "share/Debian/home/me")
        homes = other_side_homes({}, "win32", list_distros=lambda: ["Ubuntu-24.04"], shares=(str(share),))
        self.assertEqual(homes, [share / "Ubuntu-24.04/home/kim", share / "Ubuntu-24.04/home/me"])

    def test_distro_homes_fall_back_to_the_older_share_name(self):
        """\\\\wsl.localhost needs a recent Windows; \\\\wsl$ is the older name of the same share."""
        (home,) = self.mkdirs("old/Ubuntu/home/me")
        self.assertEqual(wsl_distro_homes("Ubuntu", shares=(str(self.base / "new"), str(self.base / "old"))), [home])
        self.assertEqual(wsl_distro_homes("Gone", shares=(str(self.base / "old"),)), [])

    def test_running_distros_parsed_from_wsl_exe_in_either_encoding(self):
        def wsl_exe(stdout, returncode=0):
            return lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, stdout=stdout)

        utf16 = "\ufeffUbuntu-24.04\r\nDebian\r\n".encode("utf-16-le")
        self.assertEqual(running_wsl_distros(run=wsl_exe(utf16)), ["Ubuntu-24.04", "Debian"])
        self.assertEqual(running_wsl_distros(run=wsl_exe(b"Ubuntu-24.04\nDebian\n")), ["Ubuntu-24.04", "Debian"])
        self.assertEqual(running_wsl_distros(run=wsl_exe(b"", returncode=1)), [])

        def no_wsl(*args, **kwargs):
            raise FileNotFoundError("wsl.exe")
        self.assertEqual(running_wsl_distros(run=no_wsl), [])

    def test_wsl_exe_is_the_system_copy_not_one_in_the_current_folder(self):
        """By bare name, Windows would run a wsl.exe planted in the app's or the current folder first."""
        env = {"SystemRoot": r"D:\Win"}
        self.assertEqual(wsl_exe(env, exists=lambda p: False), r"D:\Win\System32\wsl.exe")
        # A 32-bit Python on 64-bit Windows sees System32 redirected to SysWOW64, which has no wsl.exe.
        self.assertEqual(wsl_exe(env, exists=lambda p: True), r"D:\Win\Sysnative\wsl.exe")
        self.assertEqual(wsl_exe({}, exists=lambda p: False), r"C:\Windows\System32\wsl.exe")

    def test_listing_running_distros_starts_none(self):
        """`--running` lists without starting a stopped distro, and no console window flashes on Windows."""
        calls = []

        def wsl_exe(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, stdout=b"")
        running_wsl_distros(run=wsl_exe)
        ((args, kwargs),) = calls
        self.assertEqual(args[1:], ["--list", "--running", "--quiet"])
        self.assertTrue(PureWindowsPath(args[0]).is_absolute())
        self.assertEqual(PureWindowsPath(args[0]).name, "wsl.exe")
        self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertIn("timeout", kwargs)

    def test_iter_transcripts_any_depth(self):
        root = self.base / "projects"
        session = root / "proj" / "s.jsonl"
        subagent = root / "proj" / "s" / "subagents" / "agent-x.jsonl"
        for path in (session, subagent):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        (root / "stray.jsonl").write_text("", encoding="utf-8")
        found = list(iter_transcripts(root))
        self.assertEqual(sorted(found), [("proj", subagent), ("proj", session)])
        self.assertEqual(found, sorted(found))

    def lock(self, path):
        path.chmod(0)
        self.addCleanup(path.chmod, 0o700)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs POSIX permissions and a non-root user")
    def test_unreadable_projects_folder_does_not_stop_the_other_roots(self):
        """A projects folder another user owns (mode 700) passes stat() but not listing; the scan goes on."""
        locked, readable = self.mkdirs("a/projects", "b/projects/-work-repo")
        (readable / "s.jsonl").write_text(json.dumps(assistant_line()) + "\n", encoding="utf-8")
        self.lock(locked)
        err = io.StringIO()
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[locked, readable.parent]), \
                contextlib.redirect_stderr(err):
            entries, _projects = collect_all_entries()
        self.assertEqual(len(entries), 1)
        self.assertIn(f"skipping {locked}", err.getvalue())

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs POSIX permissions and a non-root user")
    def test_unreadable_project_folder_and_file_are_named(self):
        """A total that silently leaves out a folder or a file cannot be trusted; each one skipped gets a line."""
        root = self.base / "projects"
        folder = root / "-work-locked"
        file_ = root / "-work-repo" / "locked.jsonl"
        kept = root / "-work-repo" / "ok.jsonl"
        for path in (folder / "s.jsonl", file_, kept):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(assistant_line(msg_id=path.name)) + "\n", encoding="utf-8")
        self.lock(folder)
        self.lock(file_)
        err = io.StringIO()
        with mock.patch.object(track_tokens, "transcript_roots", return_value=[root]), \
                contextlib.redirect_stderr(err):
            entries, _projects = collect_all_entries()
        self.assertEqual(len(entries), 1)
        self.assertIn(f"skipping {folder}", err.getvalue())
        self.assertIn(f"skipping {file_}", err.getvalue())


class TestReportPath(unittest.TestCase):
    """Where token_usage.json goes: next to the script from source, the user's data folder in a packaged app."""

    HERE = Path("/src/ctt")
    HOME = Path("/home/u")

    def path(self, frozen, platform, env=None):
        return report_path(frozen=frozen, platform=platform, env=env or {}, home=self.HOME, here=self.HERE)

    def test_source_run_keeps_the_file_next_to_the_script(self):
        for platform in ("linux", "darwin", "win32"):
            self.assertEqual(self.path(False, platform), self.HERE / "token_usage.json")

    def test_packaged_app_writes_where_the_user_can(self):
        """Next to the code is a read-only AppImage mount, a signed .app bundle or a temp folder that vanishes."""
        name = Path("claude-token-tracker") / "token_usage.json"
        self.assertEqual(self.path(True, "linux"), self.HOME / ".local" / "share" / name)
        self.assertEqual(self.path(True, "linux", {"XDG_DATA_HOME": "/data"}), Path("/data") / name)
        self.assertEqual(self.path(True, "darwin"), self.HOME / "Library" / "Application Support" / name)
        self.assertEqual(self.path(True, "win32", {"LOCALAPPDATA": "/appdata"}), Path("/appdata") / name)
        self.assertEqual(self.path(True, "win32"), self.HOME / "AppData" / "Local" / name)


if __name__ == "__main__":
    unittest.main()
