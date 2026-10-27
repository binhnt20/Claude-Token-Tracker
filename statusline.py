#!/usr/bin/env python3
"""Status line bridge: records the plan-limit percentages Claude Code hands its status line command.

Claude Code runs the status line command after each assistant message with a JSON object on stdin. For Pro and
Max accounts that object carries `rate_limits.five_hour` and `rate_limits.seven_day` (`used_percentage`,
`resets_at`) - the only place outside Claude Code that shows them. This script appends them to
`<data dir>/limits.jsonl`, which `limits.py` reads to estimate Claude Code's part of each limit.

    python3 statusline.py --install      wrap the current status line (or add one) in ~/.claude/settings.json
    python3 statusline.py --uninstall    put the saved status line back
    python3 statusline.py [--record]     what Claude Code runs: record, then print `5h 38% · 7d 41%` (or nothing)

It reads only its stdin and the settings file: no credentials, no network.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import sys
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePath

from sources import data_dir

LOG_NAME = "limits.jsonl"
ORIGINAL_NAME = "statusline-original.json"
WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))
MIN_GAP_S = 60  # an unchanged value is written at most once a minute
TAIL_BYTES = 4096
PRUNE_AT = 16 * 1024 * 1024
PRUNE_KEEP = 8 * 1024 * 1024  # about a week of samples at a heavy user's pace, more than one weekly window
# Ends every command this script installs, so a second install or an uninstall can tell its own entry apart.
MARKER = "# claude-token-tracker statusline"
_WRAPPED = re.compile(r"^input=\$\(cat\); printf '%s' \"\$input\" \| .*? --record; "
                      r"printf '%s' \"\$input\" \| \{ (.*)\n\} " + re.escape(MARKER) + r"$", re.DOTALL)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _iso(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _epoch(text) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def sample_from(data, now: float) -> dict | None:
    """One log row from the status line input, or None when it has no usable rate limit."""
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return None
    sample = {"t": _iso(now), "session_id": data.get("session_id")}
    for kind, _label in WINDOWS:
        window = limits.get(kind)
        if isinstance(window, dict) and _number(window.get("used_percentage")):
            resets_at = window.get("resets_at")
            sample[kind] = {"pct": float(window["used_percentage"]),
                            "resets_at": int(resets_at) if _number(resets_at) else None}
    return sample if any(kind in sample for kind, _label in WINDOWS) else None


def _tail(log: Path) -> tuple[dict | None, bool]:
    """The last row that parses, and whether the file ends with a newline (a cut-off write may not)."""
    try:
        with open(log, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            chunk = f.read()
    except OSError:
        return None, True
    for line in reversed(chunk.splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            return row, chunk.endswith(b"\n") or not chunk
    return None, chunk.endswith(b"\n") or not chunk


def record(sample: dict | None, log: Path, now: float) -> bool:
    """Append the sample unless it repeats the last row within a minute. True when a row was written."""
    if sample is None:
        return False
    last, ends_clean = _tail(log)
    if last is not None and all(last.get(kind) == sample.get(kind) for kind, _label in WINDOWS):
        last_t = _epoch(last.get("t"))
        if last_t is not None and now - last_t < MIN_GAP_S:
            return False
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8", newline="\n") as f:
        f.write(("" if ends_clean else "\n") + json.dumps(sample, separators=(",", ":")) + "\n")
    prune(log)
    return True


def prune(log: Path, limit: int = PRUNE_AT, keep: int = PRUNE_KEEP) -> None:
    """Past `limit` bytes, keep the newest whole lines that fit in `keep` bytes."""
    try:
        size = log.stat().st_size
    except OSError:
        return
    if size <= limit:
        return
    tmp = log.with_name(f"{log.name}.tmp-{os.getpid()}")
    try:
        with open(log, "rb") as f:
            f.seek(size - keep)
            chunk = f.read()
        newline = chunk.find(b"\n")
        tmp.write_bytes(chunk[newline + 1:] if newline >= 0 else b"")
        os.replace(tmp, log)
    except OSError:  # another session holds the file (Windows): the next write tries again
        try:
            tmp.unlink()
        except OSError:
            pass


def compact_line(data) -> str:
    """`5h 38% · 7d 41%`, or empty when Claude Code sent no rate limits."""
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return ""
    parts = []
    for kind, label in WINDOWS:
        window = limits.get(kind)
        if isinstance(window, dict) and _number(window.get("used_percentage")):
            parts.append(f"{label} {window['used_percentage']:.0f}%")
    return " · ".join(parts)


def bridge_command(python: PurePath, script: PurePath, original: str | None = None) -> str:
    """The statusLine command. With an original command, both read the same stdin and the original's output shows.

    Paths use forward slashes: on Windows Claude Code runs the command in Git Bash, which eats backslashes.
    """
    run = f"{shlex.quote(python.as_posix())} {shlex.quote(script.as_posix())}"
    if original is None:
        return f"{run} {MARKER}"
    # The newline before `}` ends any comment the original command ends with.
    return (f"input=$(cat); printf '%s' \"$input\" | {run} --record; "
            f"printf '%s' \"$input\" | {{ {original}\n}} {MARKER}")


def is_ours(command) -> bool:
    return isinstance(command, str) and command.rstrip().endswith(MARKER)


def wrapped_original(command: str) -> str | None:
    """The original command inside an installed wrapper, or None."""
    match = _WRAPPED.match(command)
    return match.group(1) if match else None


def settings_path(env: Mapping[str, str], home: Path) -> Path:
    """Claude Code's user settings: in CLAUDE_CONFIG_DIR when set (the first one, if the tracker was given a list)."""
    config_dirs = [part.strip() for part in env.get("CLAUDE_CONFIG_DIR", "").split(",") if part.strip()]
    return (Path(config_dirs[0]).expanduser() if config_dirs else home / ".claude") / "settings.json"


def _read_json(path: Path) -> dict:
    """A JSON object from a file; {} when the file does not exist. Raises ValueError when it is not an object."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    value = json.loads(text) if text.strip() else {}
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if path.exists():
        shutil.copymode(path, tmp)  # a settings.json made private (it may hold API keys) stays private
    os.replace(tmp, path)


def _backup(settings: Path, now: float) -> Path | None:
    if not settings.exists():
        return None
    stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
    backup, n = settings.with_name(f"{settings.name}.bak-{stamp}"), 1
    while backup.exists():
        n += 1
        backup = settings.with_name(f"{settings.name}.bak-{stamp}-{n}")
    shutil.copy2(settings, backup)
    return backup


def _load(settings: Path) -> tuple[dict | None, str]:
    try:
        return _read_json(settings), ""
    except ValueError as exc:
        return None, f"{settings} is not valid JSON ({exc}); nothing changed."
    except OSError as exc:
        return None, f"Cannot read {settings} ({exc.strerror or exc}); nothing changed."


def install(settings: Path, data: Path, python: PurePath, script: PurePath, now: float) -> tuple[int, str]:
    """Point Claude Code's status line at this script, wrapping the command already there. Returns (exit code, text)."""
    current, error = _load(settings)
    if current is None:
        return 1, error
    entry = current.get("statusLine")
    command = entry.get("command") if isinstance(entry, dict) else None
    if is_ours(command):
        return 0, f"The status line bridge is already installed in {settings}."
    if entry is None:
        new_entry = {"type": "command", "command": bridge_command(python, script)}
    elif isinstance(command, str) and command.strip() and entry.get("type", "command") == "command":
        new_entry = {**entry, "type": "command", "command": bridge_command(python, script, command)}
    else:
        return 1, f"The statusLine entry in {settings} is not a command this script can wrap; nothing changed."
    try:
        saved = _read_json(data / ORIGINAL_NAME)
    except (OSError, ValueError):
        saved = {}
    saved[str(settings)] = {"statusLine": entry, "saved_at": _iso(now)}
    try:
        _write_json(data / ORIGINAL_NAME, saved)
        backup = _backup(settings, now)
        current["statusLine"] = new_entry
        _write_json(settings, current)
    except OSError as exc:
        return 1, f"Could not install ({exc.strerror or exc}); {settings} is unchanged or backed up."
    kept = "wraps your status line, which still shows" if entry else "shows `5h 38% · 7d 41%`"
    return 0, (f"Installed the status line bridge in {settings}; it {kept}.\n"
               f"Samples go to {data / LOG_NAME} after each Claude Code response (Pro/Max accounts only).\n"
               + (f"Backup of the old settings: {backup}\n" if backup else "")
               + f"Undo: {Path(sys.executable).name} {script.name} --uninstall")


def uninstall(settings: Path, data: Path, now: float) -> tuple[int, str]:
    """Put back the status line `install` replaced. Returns (exit code, text)."""
    current, error = _load(settings)
    if current is None:
        return 1, error
    entry = current.get("statusLine")
    command = entry.get("command") if isinstance(entry, dict) else None
    if not is_ours(command):
        return 0, f"The status line bridge is not installed in {settings}; nothing changed."
    try:
        saved = _read_json(data / ORIGINAL_NAME)
    except (OSError, ValueError):
        saved = {}
    record_ = saved.get(str(settings))
    if isinstance(record_, dict) and "statusLine" in record_:
        original = record_["statusLine"]
    else:
        inner = wrapped_original(command)
        original = {**entry, "command": inner} if inner is not None else None
    try:
        backup = _backup(settings, now)
        if original is None:
            current.pop("statusLine", None)
        else:
            current["statusLine"] = original
        _write_json(settings, current)
        saved.pop(str(settings), None)
        if saved:
            _write_json(data / ORIGINAL_NAME, saved)
        else:
            (data / ORIGINAL_NAME).unlink(missing_ok=True)
    except OSError as exc:
        return 1, f"Could not uninstall ({exc.strerror or exc})."
    restored = "restored your previous status line" if original else "removed the status line entry"
    return 0, f"Uninstalled the status line bridge: {restored} in {settings} (backup: {backup})."


def main(argv=None, stdin=None, stdout=None, env: Mapping[str, str] | None = None, home: Path | None = None,
         platform: str | None = None, now: float | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    platform = sys.platform if platform is None else platform
    now = time.time() if now is None else now
    data = data_dir(platform, env, home)

    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    if "--install" in argv:
        code, text = install(settings_path(env, home), data, Path(sys.executable), Path(__file__).resolve(), now)
        print(text)
        return code
    if "--uninstall" in argv:
        code, text = uninstall(settings_path(env, home), data, now)
        print(text)
        return code

    # From here on this is Claude Code's status line: nothing may raise, and an empty line beats an error.
    try:
        raw = (sys.stdin.buffer if stdin is None else stdin).read()
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        payload = None
    try:
        record(sample_from(payload, now), data / LOG_NAME, now)
    except Exception:
        pass
    if "--record" not in argv:
        try:
            out = sys.stdout.buffer if stdout is None else stdout
            out.write(compact_line(payload).encode("utf-8"))
            out.flush()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
