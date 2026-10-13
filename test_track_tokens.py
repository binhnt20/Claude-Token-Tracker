#!/usr/bin/env python3
"""Tests for transcript parsing and de-duplication. Run: python3 -m unittest test_track_tokens -v"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pricing
import track_tokens
from sources import iter_transcripts, transcript_roots
from track_tokens import (
    build_report,
    collect_all_entries,
    dedupe,
    encode_project_dir,
    entry_date,
    parse_transcript,
    project_display_name,
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
    def test_price_window_uses_utc_date(self):
        """20:00 UTC on 10-06 is 10-07 in Ho Chi Minh City; the 10-06 price still applies."""
        with mock.patch.dict(os.environ, {"TZ": "Asia/Ho_Chi_Minh"}):
            time.tzset()
            try:
                record = parse_transcript(self.write([assistant_line(
                    model="claude-sonnet-5-5", input_tokens=0, output=0, cache_read=1_000_000,
                    timestamp="2026-10-06T20:00:00Z")]))[0]
            finally:
                time.tzset()
        self.assertAlmostEqual(record["cost"]["cache_read"], 0.20)


class TestCollectAllEntries(TranscriptCase):
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


class TestProjectNames(unittest.TestCase):
    def test_encoding_matches_claude_code(self):
        self.assertEqual(encode_project_dir("/home/u/code/projects-sync/fraud-6"), "-home-u-code-projects-sync-fraud-6")
        self.assertEqual(encode_project_dir("D:\\Work\\repo"), "D--Work-repo")

    def test_matching_cwd_wins_and_home_is_tilde(self):
        cwds = {"/home/u/code/projects-sync/fraud-6", "/home/u/code/projects-sync/fraud-6/backend"}
        self.assertEqual(project_display_name("-home-u-code-projects-sync-fraud-6", cwds, home=Path("/home/u")),
                         "~/code/projects-sync/fraud-6")

    def test_windows_folder_resolves_to_its_cwd(self):
        self.assertEqual(project_display_name("D--Work-repo", {"D:\\Work\\repo"}, home=Path("/home/u")),
                         "D:\\Work\\repo")

    def test_no_matching_cwd_uses_the_shortest(self):
        cwds = {"/srv/app/sub", "/srv/app"}
        self.assertEqual(project_display_name("-srv-renamed", cwds, home=Path("/home/u")), "/srv/app")

    def test_no_cwd_keeps_the_folder_name(self):
        self.assertEqual(project_display_name("-srv-app", set(), home=Path("/home/u")), "-srv-app")

    def test_home_itself_is_tilde(self):
        self.assertEqual(project_display_name("-home-u", {"/home/u"}, home=Path("/home/u")), "~")


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
        self.assertEqual(transcript_roots(env=env, home=self.base / "home"), [a, b])

    def test_config_dir_replaces_the_defaults(self):
        (only,) = self.mkdirs("a/projects")
        self.mkdirs("home/.claude/projects")
        env = {"CLAUDE_CONFIG_DIR": str(self.base / "a")}
        self.assertEqual(transcript_roots(env=env, home=self.base / "home"), [only])

    def test_default_reads_both_home_dirs(self):
        legacy, xdg = self.mkdirs("home/.claude/projects", "home/.config/claude/projects")
        self.assertEqual(transcript_roots(env={}, home=self.base / "home"), [legacy, xdg])

    def test_missing_dirs_are_skipped(self):
        (legacy,) = self.mkdirs("home/.claude/projects")
        self.assertEqual(transcript_roots(env={}, home=self.base / "home"), [legacy])
        self.assertEqual(transcript_roots(env={}, home=self.base / "nobody"), [])

    def test_symlinked_duplicate_listed_once(self):
        (legacy,) = self.mkdirs("home/.claude/projects")
        (self.base / "home/.config").mkdir()
        try:
            (self.base / "home/.config/claude").symlink_to(self.base / "home/.claude", target_is_directory=True)
        except OSError:
            self.skipTest("cannot create symlinks here")
        self.assertEqual(transcript_roots(env={}, home=self.base / "home"), [legacy])

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


if __name__ == "__main__":
    unittest.main()
