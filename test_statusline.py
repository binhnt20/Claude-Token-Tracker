#!/usr/bin/env python3
"""Tests for the status line bridge. Run: python3 -m unittest test_statusline -v"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath

import statusline
from sources import data_dir

T0 = 1_760_000_000.0  # 2025-10-09T08:53:20Z


def payload(five=None, seven=None, session="s1", **extra):
    """The stdin JSON Claude Code hands its status line command; None leaves a window out."""
    limits = {}
    if five is not None:
        limits["five_hour"] = {"used_percentage": five, "resets_at": 1_760_010_000}
    if seven is not None:
        limits["seven_day"] = {"used_percentage": seven, "resets_at": 1_760_500_000}
    data = {"session_id": session, "transcript_path": "/x.jsonl", "model": {"display_name": "Opus"}, **extra}
    if limits:
        data["rate_limits"] = limits
    return data


class TempCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.log = self.base / "data" / "limits.jsonl"

    def lines(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestDataDir(unittest.TestCase):
    """The tracker's own folder: the packaged app's report and the bridge's samples both live there."""

    HOME = Path("/home/u")

    def test_per_platform(self):
        self.assertEqual(data_dir("linux", {}, self.HOME), self.HOME / ".local" / "share" / "claude-token-tracker")
        self.assertEqual(data_dir("linux", {"XDG_DATA_HOME": "/d"}, self.HOME), Path("/d") / "claude-token-tracker")
        self.assertEqual(data_dir("darwin", {}, self.HOME),
                         self.HOME / "Library" / "Application Support" / "claude-token-tracker")
        self.assertEqual(data_dir("win32", {"LOCALAPPDATA": "/a"}, self.HOME), Path("/a") / "claude-token-tracker")
        self.assertEqual(data_dir("win32", {}, self.HOME), self.HOME / "AppData" / "Local" / "claude-token-tracker")


class TestSample(unittest.TestCase):
    def test_both_windows(self):
        sample = statusline.sample_from(payload(five=23.5, seven=41), T0)
        self.assertEqual(sample["t"], "2025-10-09T08:53:20.000Z")
        self.assertEqual(sample["session_id"], "s1")
        self.assertEqual(sample["five_hour"], {"pct": 23.5, "resets_at": 1_760_010_000})
        self.assertEqual(sample["seven_day"], {"pct": 41.0, "resets_at": 1_760_500_000})

    def test_one_window(self):
        sample = statusline.sample_from(payload(five=10), T0)
        self.assertIn("five_hour", sample)
        self.assertNotIn("seven_day", sample)

    def test_no_rate_limits_is_no_sample(self):
        """API-key users never get rate_limits; Pro/Max sessions get them only after the first response."""
        self.assertIsNone(statusline.sample_from(payload(), T0))
        self.assertIsNone(statusline.sample_from({"rate_limits": None}, T0))
        self.assertIsNone(statusline.sample_from([], T0))

    def test_bad_values_are_left_out(self):
        bad = {"rate_limits": {"five_hour": {"used_percentage": "12", "resets_at": 1},
                               "seven_day": {"used_percentage": True, "resets_at": 1}}}
        self.assertIsNone(statusline.sample_from(bad, T0))
        no_reset = {"rate_limits": {"five_hour": {"used_percentage": 12}}}
        self.assertEqual(statusline.sample_from(no_reset, T0)["five_hour"], {"pct": 12.0, "resets_at": None})


class TestRecord(TempCase):
    def record(self, data, now):
        return statusline.record(statusline.sample_from(data, now), self.log, now)

    def test_first_sample_is_written(self):
        self.assertTrue(self.record(payload(five=5, seven=1), T0))
        self.assertEqual(len(self.lines()), 1)

    def test_unchanged_within_a_minute_is_skipped(self):
        """The status line re-runs on every assistant message and on mode changes; most runs repeat the last value."""
        self.record(payload(five=5, seven=1), T0)
        self.assertFalse(self.record(payload(five=5, seven=1, session="s2"), T0 + 59))
        self.assertEqual(len(self.lines()), 1)

    def test_unchanged_after_a_minute_is_written(self):
        """An unchanged sample still dates the last time nothing rose, which narrows the estimate."""
        self.record(payload(five=5, seven=1), T0)
        self.assertTrue(self.record(payload(five=5, seven=1), T0 + 60))
        self.assertEqual(len(self.lines()), 2)

    def test_a_change_is_written_at_once(self):
        self.record(payload(five=5, seven=1), T0)
        self.assertTrue(self.record(payload(five=5.5, seven=1), T0 + 1))
        self.assertEqual([line["five_hour"]["pct"] for line in self.lines()], [5.0, 5.5])

    def test_a_broken_last_line_does_not_stop_the_log(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_text('{"t": "2025-10-09T08:53:20.000Z", "five_hour": {"pc', encoding="utf-8")
        self.assertTrue(self.record(payload(five=5), T0))
        last = self.log.read_text(encoding="utf-8").splitlines()[-1]
        self.assertEqual(json.loads(last)["five_hour"]["pct"], 5.0)

    def test_none_is_not_written(self):
        self.assertFalse(statusline.record(None, self.log, T0))
        self.assertFalse(self.log.exists())

    def test_a_large_log_keeps_its_newest_lines(self):
        self.log.parent.mkdir(parents=True)
        old = [json.dumps({"t": f"2025-01-01T00:00:{i % 60:02d}.000Z", "n": i}) for i in range(200)]
        self.log.write_text("\n".join(old) + "\n", encoding="utf-8")
        statusline.prune(self.log, limit=2_000, keep=1_000)
        kept = self.lines()
        self.assertLessEqual(self.log.stat().st_size, 1_000)
        self.assertEqual(kept[-1]["n"], 199)
        self.assertEqual([line["n"] for line in kept], list(range(kept[0]["n"], 200)))

    def test_a_small_log_is_left_alone(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_text('{"n": 1}\n', encoding="utf-8")
        statusline.prune(self.log, limit=2_000, keep=1_000)
        self.assertEqual(self.lines(), [{"n": 1}])


class TestCompactLine(unittest.TestCase):
    def test_line(self):
        self.assertEqual(statusline.compact_line(payload(five=38.4, seven=41)), "5h 38% · 7d 41%")
        self.assertEqual(statusline.compact_line(payload(five=38)), "5h 38%")
        self.assertEqual(statusline.compact_line(payload()), "")


class TestRun(TempCase):
    def run_bridge(self, args, stdin_text, env=None):
        out = io.BytesIO()
        env = {"XDG_DATA_HOME": str(self.base / "xdg"), **(env or {})}
        code = statusline.main(args, stdin=io.BytesIO(stdin_text.encode("utf-8")), stdout=out, env=env,
                               home=self.base / "home", platform="linux", now=T0)
        return code, out.getvalue().decode("utf-8")

    def test_default_records_and_prints(self):
        code, out = self.run_bridge([], json.dumps(payload(five=38, seven=41)))
        self.assertEqual((code, out), (0, "5h 38% · 7d 41%"))
        log = self.base / "xdg" / "claude-token-tracker" / "limits.jsonl"
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_record_prints_nothing(self):
        code, out = self.run_bridge(["--record"], json.dumps(payload(five=38)))
        self.assertEqual((code, out), (0, ""))
        self.assertTrue((self.base / "xdg" / "claude-token-tracker" / "limits.jsonl").exists())

    def test_never_fails_the_status_line(self):
        """An empty status line is better than an error in it."""
        self.assertEqual(self.run_bridge([], "not json"), (0, ""))
        self.assertEqual(self.run_bridge([], ""), (0, ""))
        blocked = self.base / "file"
        blocked.write_text("x", encoding="utf-8")
        code, out = self.run_bridge([], json.dumps(payload(five=7)), env={"XDG_DATA_HOME": str(blocked)})
        self.assertEqual((code, out), (0, "5h 7%"))


class TestCommand(unittest.TestCase):
    def test_no_original(self):
        command = statusline.bridge_command(PurePosixPath("/usr/bin/python3"), PurePosixPath("/opt/ctt/statusline.py"))
        self.assertEqual(command, "/usr/bin/python3 /opt/ctt/statusline.py " + statusline.MARKER)

    def test_windows_paths_use_forward_slashes(self):
        """Claude Code runs the command in Git Bash on Windows, which eats backslashes."""
        command = statusline.bridge_command(PureWindowsPath(r"C:\Program Files\Python312\python.exe"),
                                            PureWindowsPath(r"C:\Users\a b\ctt\statusline.py"))
        self.assertTrue(command.startswith("'C:/Program Files/Python312/python.exe' 'C:/Users/a b/ctt/statusline.py'"))
        self.assertNotIn("\\", command)

    def test_wraps_the_original(self):
        command = statusline.bridge_command(PurePosixPath("/py"), PurePosixPath("/s.py"), "orca-line --x")
        self.assertEqual(command, 'input=$(cat); printf \'%s\' "$input" | /py /s.py --record; '
                                  'printf \'%s\' "$input" | { orca-line --x\n} ' + statusline.MARKER)

    def test_original_comes_back_out(self):
        command = statusline.bridge_command(PurePosixPath("/py"), PurePosixPath("/s.py"), "a | b # note")
        self.assertEqual(statusline.wrapped_original(command), "a | b # note")
        self.assertIsNone(statusline.wrapped_original("echo hi"))


class TestInstall(TempCase):
    PY = PurePosixPath("/usr/bin/python3")
    SCRIPT = PurePosixPath("/opt/ctt/statusline.py")

    def setUp(self):
        super().setUp()
        self.settings = self.base / "home" / ".claude" / "settings.json"
        self.data = self.base / "data"

    def write_settings(self, value):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        text = value if isinstance(value, str) else json.dumps(value, indent=2)
        self.settings.write_text(text, encoding="utf-8")
        return text

    def read_settings(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def install(self):
        return statusline.install(self.settings, self.data, self.PY, self.SCRIPT, now=T0)

    def uninstall(self):
        return statusline.uninstall(self.settings, self.data, now=T0)

    def backups(self):
        return sorted(self.settings.parent.glob("settings.json.bak-*"))

    def test_fresh_install(self):
        code, message = self.install()
        self.assertEqual(code, 0)
        self.assertEqual(self.read_settings()["statusLine"],
                         {"type": "command", "command": statusline.bridge_command(self.PY, self.SCRIPT)})
        self.assertIn("Installed", message)

    def test_wraps_an_existing_status_line_and_keeps_everything_else(self):
        orca = {"type": "command", "command": "bash ~/.orca/agent-hooks/claude-statusline.sh", "padding": 0}
        old_text = self.write_settings({"model": "opus", "statusLine": orca, "hooks": {"Stop": []}})
        code, _ = self.install()
        self.assertEqual(code, 0)
        settings = self.read_settings()
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["hooks"], {"Stop": []})
        self.assertEqual(settings["statusLine"]["padding"], 0)
        self.assertEqual(settings["statusLine"]["command"],
                         statusline.bridge_command(self.PY, self.SCRIPT, orca["command"]))
        (backup,) = self.backups()
        self.assertEqual(backup.read_text(encoding="utf-8"), old_text)
        saved = json.loads((self.data / "statusline-original.json").read_text(encoding="utf-8"))
        self.assertEqual(saved[str(self.settings)]["statusLine"], orca)

    def test_install_twice_wraps_once(self):
        self.write_settings({"statusLine": {"type": "command", "command": "orca"}})
        self.install()
        first = self.read_settings()
        code, message = self.install()
        self.assertEqual(code, 0)
        self.assertIn("already", message)
        self.assertEqual(self.read_settings(), first)

    def test_uninstall_restores_the_original(self):
        orca = {"type": "command", "command": "orca", "padding": 2}
        self.write_settings({"statusLine": orca, "theme": "dark"})
        self.install()
        code, _ = self.uninstall()
        self.assertEqual(code, 0)
        self.assertEqual(self.read_settings(), {"statusLine": orca, "theme": "dark"})
        self.assertFalse((self.data / "statusline-original.json").exists())

    def test_uninstall_without_an_original_removes_the_entry(self):
        self.write_settings({"theme": "dark"})
        self.install()
        self.uninstall()
        self.assertEqual(self.read_settings(), {"theme": "dark"})

    def test_uninstall_without_the_saved_file_unwraps_the_command(self):
        self.write_settings({"statusLine": {"type": "command", "command": "orca --x"}})
        self.install()
        (self.data / "statusline-original.json").unlink()
        self.uninstall()
        self.assertEqual(self.read_settings()["statusLine"], {"type": "command", "command": "orca --x"})

    def test_uninstall_leaves_a_foreign_status_line(self):
        self.write_settings({"statusLine": {"type": "command", "command": "orca"}})
        code, message = self.uninstall()
        self.assertEqual(code, 0)
        self.assertIn("not installed", message)
        self.assertEqual(self.read_settings(), {"statusLine": {"type": "command", "command": "orca"}})
        self.assertEqual(self.backups(), [])

    def test_unreadable_settings_are_never_overwritten(self):
        text = self.write_settings('{"theme": "dark",')
        code, message = self.install()
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", message)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), text)


@unittest.skipIf(sys.platform == "win32" or not shutil.which("bash"), "needs a POSIX bash (Windows: checked by hand)")
class TestCommandInBash(TempCase):
    """The installed command, run the way Claude Code runs it: the original sees the same stdin and prints the line."""

    def test_wrapped_command_records_and_shows_the_original(self):
        script = Path(statusline.__file__).resolve()
        original = (f"{sys.executable} -c \"import json,sys; print('ORIG', json.load(sys.stdin)['session_id'])\"")
        command = statusline.bridge_command(Path(sys.executable), script, original)
        env = {**os.environ, "XDG_DATA_HOME": str(self.base / "xdg")}
        result = subprocess.run(["bash", "-c", command], input=json.dumps(payload(five=12, session="abc")),
                                capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "ORIG abc\n", ""))
        log = self.base / "xdg" / "claude-token-tracker" / "limits.jsonl"
        self.assertEqual(json.loads(log.read_text(encoding="utf-8"))["five_hour"]["pct"], 12.0)

    def test_bare_command_prints_the_compact_line(self):
        command = statusline.bridge_command(Path(sys.executable), Path(statusline.__file__).resolve())
        env = {**os.environ, "XDG_DATA_HOME": str(self.base / "xdg")}
        result = subprocess.run(["bash", "-c", command], input=json.dumps(payload(five=12, seven=3)).encode("utf-8"),
                                capture_output=True, env=env, timeout=30)
        self.assertEqual((result.returncode, result.stdout.decode("utf-8")), (0, "5h 12% · 7d 3%"))


if __name__ == "__main__":
    unittest.main()
