#!/usr/bin/env python3
"""Claude Code's share of the plan limits, estimated from status line samples and this computer's transcripts.

The 5-hour and weekly limits are one pool for claude.ai chat, Claude Code, Cowork and every device, and
Anthropic publishes no token-to-percent rule. statusline.py records the official `used_percentage` after each
Claude Code response. Per window, each rise between two samples is credited to Claude Code when Claude Code calls
of this computer fall inside the gap:

- a gap of at most 10 minutes: the whole rise;
- a longer gap: min(rise, k x the calls' API-equivalent cost), where k (% per dollar) is fitted per window kind on
  the short gaps; with no fit yet, the whole rise;
- a gap with no Claude Code call (chat in a break, another device): nothing.

The window start (`resets_at` minus its length, give or take the hour `resets_at` is rounded to) counts as 0 %. The
result never exceeds the account %. Two accounts may share a reset time: after a switch the readings drop, and the
new account gets its own window, counted from its first reading.
"""

from __future__ import annotations

import bisect
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone, tzinfo
from pathlib import Path

from sources import data_dir, other_side_homes

LOG_NAME = "limits.jsonl"
KINDS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}
LABELS = {"five_hour": "5-hour", "seven_day": "weekly"}
SHORT_GAP_S = 600
CALL_SLACK_S = 5  # a sample is written just after the response whose call it reports
SAME_WINDOW_S = 600  # reset times this close name one window
# Claude Code reports resets_at rounded to the hour, so a window may open up to an hour before resets_at minus its
# length: the first gap also takes the calls of that hour.
ROUNDING_S = 3600
# An account switch shows only as a drop when both accounts reset at the same time (15-10: weekly, Sat 00:00Z).
# SWITCH_CONFIRM readings in a row SWITCH_DROP points or more below the account's level are the new account; fewer are
# idle sessions re-running their status line with an older value (14-10: 67 % while the account stood at 77 %).
SWITCH_DROP = 10
SWITCH_CONFIRM = 3


def _epoch(text) -> float | None:
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_paths(env: Mapping[str, str], platform: str, home: Path, other_homes: Iterable[Path]) -> list[Path]:
    """This side's sample log, then the other side's on a Windows + WSL computer: each runs its own bridge."""
    paths = [data_dir(platform, env, home) / LOG_NAME]
    other = "win32" if platform.startswith("linux") else "linux" if platform == "win32" else None
    if other:
        paths += [data_dir(other, {}, h) / LOG_NAME for h in other_homes]
    return paths


def parse_samples(rows: Iterable) -> list[dict]:
    """Rows with a readable time, each given `_t` (epoch seconds), sorted by time."""
    samples = []
    for row in rows:
        if isinstance(row, dict) and (t := _epoch(row.get("t"))) is not None:
            samples.append({**row, "_t": t})
    return sorted(samples, key=lambda s: s["_t"])


def read_samples(paths: Iterable[Path]) -> list[dict]:
    rows = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return parse_samples(rows)


def _windows(samples: list[dict], kind: str) -> list[dict]:
    """Samples of one kind grouped by reset time, then by account (_accounts):
    [{resets_at, points: [(t, pct)], sessions, opens, live}]."""
    groups: list[dict] = []
    for s in samples:
        reading = s.get(kind)
        if not isinstance(reading, dict) or not isinstance(reading.get("pct"), (int, float)):
            continue
        resets_at = reading.get("resets_at")
        if not isinstance(resets_at, (int, float)):
            continue
        group = next((g for g in groups if abs(g["resets_at"] - resets_at) <= SAME_WINDOW_S), None)
        if group is None:
            group = {"resets_at": resets_at, "readings": []}
            groups.append(group)
        group["resets_at"] = resets_at
        group["readings"].append((s["_t"], float(reading["pct"]), s.get("session_id")))
    return [w for g in groups for w in _accounts(g["resets_at"], g["readings"])]


def _accounts(resets_at: float, readings: list[tuple]) -> list[dict]:
    """One window per account among the time-sorted (t, pct, session) readings of one reset time. A lower reading is
    a stale re-run and stays with the account, unless SWITCH_CONFIRM in a row sit SWITCH_DROP points below its level:
    they open the next account. After a switch, a session repeating its own reading above the level is an idle session
    of an earlier account and is left out. `opens`: the window starts at 0 % (first account); `live`: the last one."""
    accounts: list[dict] = []
    low: list[tuple] = []
    last_own: dict = {}

    def add(account: dict, t: float, pct: float, session) -> None:
        account["points"].append((t, pct))
        account["sessions"].add(session)
        account["level"] = max(account["level"], pct)

    def open_account(batch: list[tuple]) -> None:
        account = {"resets_at": resets_at, "points": [], "sessions": set(), "level": 0.0, "opens": not accounts}
        for reading in batch:
            add(account, *reading)
        accounts.append(account)

    for t, pct, session in readings:
        own, last_own[session] = last_own.get(session), pct
        if not accounts:
            open_account([(t, pct, session)])
            continue
        account = accounts[-1]
        if len(accounts) > 1 and pct > account["level"] and pct == own:
            continue
        if pct <= account["level"] - SWITCH_DROP:
            low.append((t, pct, session))
            if len(low) >= SWITCH_CONFIRM:
                open_account(low)
                low = []
            continue
        if pct >= account["level"]:
            for reading in low:
                add(account, *reading)
            low = []
        add(account, t, pct, session)
    for reading in low:
        add(accounts[-1], *reading)
    for account in accounts:
        account["live"] = account is accounts[-1]
    return accounts


class _Calls:
    """Claude Code calls sorted by time, for counting the ones inside a gap."""

    def __init__(self, entries: Iterable[dict]):
        calls = []
        for e in entries:
            t = _epoch(e.get("timestamp"))
            if t is None:
                continue
            cost = e.get("cost")
            usd = (cost.get("total", sum(v for v in cost.values() if isinstance(v, (int, float))))
                   if isinstance(cost, dict) else 0.0)
            calls.append((t, usd, e.get("session_id")))
        calls.sort(key=lambda c: c[0])
        self.calls = calls
        self.times = [c[0] for c in calls]

    def between(self, low: float, high: float, excluded: set) -> tuple[int, float]:
        """(count, dollars) of calls with low < t <= high, leaving out the excluded sessions."""
        n, usd = 0, 0.0
        for t, cost, session in self.calls[bisect.bisect_right(self.times, low):bisect.bisect_right(self.times, high)]:
            if session not in excluded:
                n += 1
                usd += cost
        return n, usd


def _gaps(window: dict, length: int, calls: _Calls, excluded: set) -> list[tuple[float, float, int, float]]:
    """(rise, seconds, calls, dollars) for each pair of consecutive points, from the 0 % window start (from the first
    reading for an account switched to inside the window)."""
    points = sorted(window["points"])
    opens = window["opens"]
    first = (min(window["resets_at"] - length, points[0][0]), 0.0) if opens else points[0]
    kept, running = [first], first[1]
    for t, pct in points if opens else points[1:]:
        if pct >= running:  # lower = a stale re-run of an older value
            kept.append((t, pct))
            running = pct
    gaps = []
    for i, ((t0, p0), (t1, p1)) in enumerate(zip(kept, kept[1:])):
        low = t0 - ROUNDING_S if i == 0 and opens else t0 + CALL_SLACK_S
        n, usd = calls.between(low, t1 + CALL_SLACK_S, excluded)
        gaps.append((p1 - p0, t1 - t0, n, usd))
    return gaps


def claude_code_share(samples: list[dict], entries: Iterable[dict], now: float) -> dict:
    """Every window with its account % and Claude Code's estimated part, newest first, and the current one per kind."""
    calls = _Calls(entries)
    result = {"windows": [], "current": {kind: None for kind in KINDS}, "samples": len(samples), "pct_per_usd": {}}
    for kind, length in KINDS.items():
        windows = _windows(samples, kind)
        all_sessions = set().union(*(w["sessions"] for w in windows)) if windows else set()
        # A session that reported only to another window of this kind runs on another account (or before a reset).
        gaps = {id(w): _gaps(w, length, calls, all_sessions - w["sessions"]) for w in windows}
        short = [(rise, usd) for g in gaps.values() for rise, secs, n, usd in g if n and secs <= SHORT_GAP_S]
        dollars = sum(usd for _rise, usd in short)
        k = sum(rise for rise, _usd in short) / dollars if dollars > 0 else None
        result["pct_per_usd"][kind] = k
        for w in windows:
            measured = estimated = 0.0
            for rise, secs, n, usd in gaps[id(w)]:
                if not n:
                    continue
                if secs <= SHORT_GAP_S:
                    measured += rise
                else:
                    estimated += rise if k is None else min(rise, k * usd)
            account = max(pct for _t, pct in w["points"])
            result["windows"].append({
                "kind": kind,
                "resets_at": int(w["resets_at"]),
                "start": int(w["resets_at"] - length),
                "account_pct": round(account, 2),
                "claude_code_pct": round(min(measured + estimated, account), 2),
                "measured_pct": round(measured, 2),
                "estimated_pct": round(estimated, 2),
                "as_of": _iso(max(t for t, _pct in w["points"])),
                "samples": len(w["points"]),
                "current": w["resets_at"] > now and w["live"],
            })
    result["windows"].sort(key=lambda w: (w["resets_at"], w["as_of"]), reverse=True)
    for w in result["windows"]:
        if w["current"] and result["current"][w["kind"]] is None:
            result["current"][w["kind"]] = w
    return result


def describe(window: dict, tz: tzinfo | None = None, lead: bool = True) -> str:
    """`Claude Code used 30.2% of the 5-hour limit (account total 38.0%) · resets 14:05 · as of 13:52`;
    without `lead`, from "of the" on (the dashboard shows the number beside it)."""
    def local(t: float, fmt: str) -> str:
        return datetime.fromtimestamp(t, timezone.utc).astimezone(tz).strftime(fmt)

    reset_fmt = "%H:%M" if window["kind"] == "five_hour" else "%a %H:%M"
    head = f"Claude Code used {window['claude_code_pct']:.1f}% " if lead else ""
    return (f"{head}of the {LABELS[window['kind']]} limit "
            f"(account total {window['account_pct']:.1f}%) · resets {local(window['resets_at'], reset_fmt)} · "
            f"as of {local(_epoch(window['as_of']), '%H:%M')}")


def report(entries: Iterable[dict], env: Mapping[str, str] | None = None, home: Path | None = None,
           platform: str | None = None, other_homes: list[Path] | None = None, now: float | None = None) -> dict:
    """claude_code_share over both sides' sample logs, plus the logs read and the command that starts the samples."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    platform = sys.platform if platform is None else platform
    if other_homes is None:
        other_homes = other_side_homes(env, platform)
    paths = log_paths(env, platform, home, other_homes)
    share = claude_code_share(read_samples(paths), entries, time.time() if now is None else now)
    share["logs"] = [str(p) for p in paths]
    share["enable"] = f"{'python' if platform == 'win32' else 'python3'} statusline.py --install"
    return share


def summary_lines(share: dict, tz: tzinfo | None = None) -> list[str]:
    """One line per limit for the current windows, or how to start recording."""
    if not share.get("samples"):
        return [f"No status line samples yet. Turn them on: {share.get('enable')} "
                "(Pro and Max accounts; Claude Code shows the limits only to them)."]
    return [describe(w, tz) if (w := share["current"].get(kind)) else f"No sample in the current {LABELS[kind]} window yet."
            for kind in KINDS]
