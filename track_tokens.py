#!/usr/bin/env python3
"""
Claude Code Token Usage Tracker (MVP)
Scans Claude Code transcripts (sources.py lists the folders) and aggregates token usage into a JSON report.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from pricing import COST_PARTS, cost_usd, normalize_model
from sources import iter_transcripts, transcript_candidates, transcript_roots

# Kept for callers that import it; collect_all_entries reads every root from sources.transcript_roots().
CLAUDE_DIR = Path.home() / ".claude" / "projects"
OUTPUT_FILE = Path(__file__).parent / "token_usage.json"

TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_creation_5m_tokens",
    "cache_creation_1h_tokens",
    "cache_read_input_tokens",
)

# Cache reads are excluded: they run an order of magnitude above every other count and
# would swamp the totals.
BILLABLE_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens")

# Claude Code writes this model name on lines it makes up itself (an interrupted turn, an error); no API call is behind
# them, so they are not requests.
SYNTHETIC_MODEL = "<synthetic>"


class TranscriptsNotFound(Exception):
    """No transcripts folder exists; `candidates` are the folders that were looked in."""

    def __init__(self, candidates: list[Path]):
        self.candidates = candidates
        looked_in = "\n".join(f"  {c}" for c in candidates)
        super().__init__(f"No Claude Code transcripts folder found. Looked in:\n{looked_in}\n"
                         "Set CLAUDE_CONFIG_DIR if Claude Code keeps its data somewhere else.")


def fmt_num(n: int) -> str:
    """Format number to human-readable string: 1234 -> '1.2K', 1234567 -> '1.2M'."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def add_display(stats: dict) -> dict:
    """Add '_display' keys with human-readable values alongside raw numbers."""
    out = {}
    for k, v in stats.items():
        out[k] = v
        if isinstance(v, int) and "tokens" in k or k == "requests":
            out[k + "_display"] = fmt_num(v)
    return out


def new_stats() -> dict:
    """Return an empty stats bucket."""
    stats = {k: 0 for k in TOKEN_KEYS}
    stats["requests"] = 0
    stats["cost"] = {part: 0.0 for part in COST_PARTS}
    stats["cost"]["total"] = 0.0
    # Counts, not a flag: a bucket keyed by project mixes models, so "is this unpriced?"
    # has no single answer. Counting lets a bucket be partially priced.
    stats["unpriced_requests"] = 0
    stats["unpriced_tokens"] = 0
    return stats


def _parse_timestamp(timestamp: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # A timestamp without an offset is taken as UTC, which is what Claude Code writes.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def entry_date(timestamp: str, tz: tzinfo | None = None) -> str | None:
    """The YYYY-MM-DD for an ISO timestamp in tz (default: the user's local zone), or None when it cannot be parsed."""
    dt = _parse_timestamp(timestamp)
    return dt.astimezone(tz).strftime("%Y-%m-%d") if dt else None


def utc_date(timestamp: str) -> str | None:
    """The UTC YYYY-MM-DD for an ISO timestamp, or None. Price windows are UTC dates."""
    dt = _parse_timestamp(timestamp)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d") if dt else None


def total_tokens(stats: dict) -> int:
    """Billable token total: input + output + cache writes."""
    return sum(stats[k] for k in BILLABLE_TOKEN_KEYS)


def accumulate(stats: dict, entry: dict) -> None:
    """Add one entry's tokens, cost and request count into a stats bucket."""
    for k in TOKEN_KEYS:
        stats[k] += entry[k]
    stats["requests"] += 1

    if entry["cost"]:
        for k, v in entry["cost"].items():
            stats["cost"][k] += v
    else:
        stats["unpriced_requests"] += 1
        stats["unpriced_tokens"] += total_tokens(entry)


def _count(mapping: dict, key: str) -> int:
    """A token or request count from a usage object; a missing or null field counts as 0."""
    value = mapping.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def record_key(entry: dict, message: dict) -> str | None:
    """Identity of the API call behind a transcript line, or None when the line carries no id."""
    message_id = message.get("id")
    if message_id:
        if entry.get("requestId"):
            return f"{message_id}:{entry['requestId']}"
        if entry.get("sessionId"):
            return f"{message_id}:{entry['sessionId']}"
    if entry.get("uuid"):
        return f"uuid:{entry['uuid']}"
    return None


def dedupe(records: list[dict]) -> list[dict]:
    """Keep one record per API call: the line with the most output tokens, the later one on a tie.

    Claude Code writes one line per content block of a response, each repeating the call's usage, and the first
    line's output count is still partial. Resumed sessions also copy earlier lines into a new file.
    """
    kept = {}
    for record in records:
        current = kept.get(record["key"])
        if current is None or record["output_tokens"] >= current["output_tokens"]:
            kept[record["key"]] = record
    return list(kept.values())


def parse_transcript(filepath: Path) -> list[dict]:
    """Parse a JSONL transcript file into one record per usage line, each carrying its own cost."""
    entries = []
    try:
        # A byte that is not UTF-8 becomes U+FFFD, so it costs at most its own line, never the rest of the file.
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(entry, dict):
                    continue

                if entry.get("type") != "assistant":
                    continue

                message = entry.get("message")
                if not message or not isinstance(message, dict):
                    continue

                usage = message.get("usage")
                if not usage or not isinstance(usage, dict):
                    continue

                timestamp = entry.get("timestamp")
                if not timestamp or message.get("model") == SYNTHETIC_MODEL:
                    continue

                # Canonical name: snapshot suffixes price identically, so folding them here
                # keeps one series per model everywhere downstream. The key can be present
                # and null, which "unknown" as a get() default would not catch.
                price_model = normalize_model(message.get("model") or "unknown")
                session_id = entry.get("sessionId", "unknown")
                speed = usage.get("speed")
                # Fast mode is its own series on the charts; pricing applies its multiplier.
                model = f"{price_model} (fast)" if speed == "fast" else price_model
                server_tools = usage.get("server_tool_use")
                web_searches = _count(server_tools, "web_search_requests") if isinstance(server_tools, dict) else 0

                cache_creation = usage.get("cache_creation")
                unmodelled_cache = False
                if isinstance(cache_creation, dict):
                    cache_5m = _count(cache_creation, "ephemeral_5m_input_tokens")
                    cache_1h = _count(cache_creation, "ephemeral_1h_input_tokens")
                    # Derive the total from the split rather than reading
                    # cache_creation_input_tokens: on a handful of entries the top-level
                    # figure under-reports the split, and deriving keeps the counters and
                    # the cost that bills them from ever disagreeing.
                    cache_write_total = cache_5m + cache_1h
                    # A cache tier we do not model would bill as zero — the exact silent
                    # under-billing this module exists to stop. Refuse to price it instead.
                    declared = sum(v for v in cache_creation.values() if isinstance(v, int))
                    unmodelled_cache = declared != cache_write_total
                else:
                    # Older transcripts carry only the total, with no 5m/1h split.
                    cache_write_total = _count(usage, "cache_creation_input_tokens")
                    cache_5m = cache_write_total
                    cache_1h = 0

                record = {
                    "key": record_key(entry, message) or f"line:{filepath}:{lineno}",
                    "timestamp": timestamp,
                    "model": model,
                    "price_model": price_model,
                    "session_id": session_id,
                    "cwd": entry.get("cwd"),
                    "is_sidechain": bool(entry.get("isSidechain")),
                    "speed": speed,
                    "inference_geo": usage.get("inference_geo"),
                    "web_search_requests": web_searches,
                    "input_tokens": _count(usage, "input_tokens"),
                    "output_tokens": _count(usage, "output_tokens"),
                    "cache_creation_input_tokens": cache_write_total,
                    "cache_creation_5m_tokens": cache_5m,
                    "cache_creation_1h_tokens": cache_1h,
                    "cache_read_input_tokens": _count(usage, "cache_read_input_tokens"),
                }

                # Price windows follow the UTC date of the call.
                date = utc_date(timestamp)
                if date and not unmodelled_cache:
                    record["cost"] = cost_usd(record, price_model, date)
                else:
                    record["cost"] = None
                entries.append(record)
    except OSError:
        pass
    return entries


def encode_project_dir(cwd: str) -> str:
    """The folder name Claude Code gives a working directory: every character outside [A-Za-z0-9-] becomes '-'."""
    return re.sub(r"[^A-Za-z0-9-]", "-", cwd)


def project_display_name(dir_name: str, cwds: Iterable[str], home: Path | None = None) -> str:
    """Name a project folder by the working directory it was created for.

    The folder name cannot be decoded ('-' stands for '/', '.', '_' and '-' alike), so the cwd recorded in the
    transcripts is used: the one that encodes to the folder name, else the shortest, else the folder name.
    """
    cwds = sorted(set(cwds), key=lambda c: (len(c), c))
    matching = [c for c in cwds if encode_project_dir(c) == dir_name]
    name = matching[0] if matching else cwds[0] if cwds else None
    if name is None:
        return dir_name
    home_str = str(Path.home() if home is None else home).rstrip("/\\")
    # Windows paths ignore case: C:\Users\binh is the home of C:\Users\Binh\repo. The shown name keeps its own case.
    same = (lambda a, b: a.casefold() == b.casefold()) if re.match(r"[A-Za-z]:", home_str) else str.__eq__
    head, rest = name[:len(home_str)], name[len(home_str):]
    if not home_str or not same(head, home_str):
        return name
    if not rest:
        return "~"
    return "~" + rest if rest[0] in "/\\" else name


def add_stats(into: dict, stats: dict) -> None:
    """Add one stats bucket into another."""
    for k in TOKEN_KEYS + ("requests", "unpriced_requests", "unpriced_tokens"):
        into[k] += stats[k]
    for k, v in stats["cost"].items():
        into["cost"][k] += v


def aggregate(entries: list[dict], date_filter: str | None = None) -> tuple[dict, dict]:
    """Aggregate usage by local date and model, and by local date and project, in one pass."""
    by_date = defaultdict(lambda: defaultdict(new_stats))
    by_date_project = defaultdict(lambda: defaultdict(new_stats))

    for e in entries:
        date_str = entry_date(e["timestamp"])
        if not date_str:
            continue
        if date_filter and date_str != date_filter:
            continue

        accumulate(by_date[date_str][e["model"]], e)
        accumulate(by_date_project[date_str][e.get("project", "unknown")], e)

    return dict(by_date), dict(by_date_project)


def collect_all_entries() -> tuple[list[dict], dict]:
    """Scan all projects and collect token usage entries."""
    all_entries = []
    project_totals = defaultdict(new_stats)

    roots = transcript_roots()
    if not roots:
        raise TranscriptsNotFound(transcript_candidates())

    by_dir = defaultdict(list)
    for root in roots:
        for dir_name, jsonl_file in iter_transcripts(root):
            by_dir[dir_name].extend(parse_transcript(jsonl_file))

    for dir_name, records in by_dir.items():
        project_name = project_display_name(dir_name, {r["cwd"] for r in records if r.get("cwd")})
        for record in records:
            record["project"] = project_name
        all_entries.extend(records)

    # De-duplicate across every file before any total: copies of a call can sit in different files.
    all_entries = dedupe(all_entries)
    for record in all_entries:
        accumulate(project_totals[record["project"]], record)

    return all_entries, dict(project_totals)


def finalize_stats(stats: dict) -> dict:
    """Null the cost of fully-unpriced buckets, then add display strings."""
    out = dict(stats)
    if out["requests"] and out["unpriced_requests"] == out["requests"]:
        # Nothing here was priced, so a zeroed cost would read as "$0.00 spent"
        # instead of "not priced". A partially priced bucket keeps its real subtotal.
        out["cost"] = None
    return add_display(out)


def build_report(all_entries: list[dict], project_totals: dict, date_filter: str | None = None) -> dict:
    """Build the final report.

    project_totals is kept for callers; by_project is re-derived from the entries so it follows date_filter.
    """
    by_date, by_date_project = aggregate(all_entries, date_filter)

    grand = new_stats()
    unpriced_models = set()

    for date_models in by_date.values():
        for model, stats in date_models.items():
            for k in TOKEN_KEYS:
                grand[k] += stats[k]
            grand["requests"] += stats["requests"]
            grand["unpriced_requests"] += stats["unpriced_requests"]
            grand["unpriced_tokens"] += stats["unpriced_tokens"]
            for k, v in stats["cost"].items():
                grand["cost"][k] += v
            if stats["unpriced_requests"]:
                unpriced_models.add(model)

    grand["total_tokens"] = total_tokens(grand)
    grand["unpriced_models"] = sorted(unpriced_models)

    # Sort dates descending, add display values
    sorted_dates = {}
    for date in sorted(by_date.keys(), reverse=True):
        sorted_dates[date] = {model: finalize_stats(s) for model, s in by_date[date].items()}

    projects = defaultdict(new_stats)
    for day_projects in by_date_project.values():
        for name, stats in day_projects.items():
            add_stats(projects[name], stats)

    def by_tokens(items):
        return sorted(items, key=lambda x: total_tokens(x[1]), reverse=True)

    # Sort projects by billable tokens descending, add display values
    sorted_projects = {name: finalize_stats(stats) for name, stats in by_tokens(projects.items())}
    sorted_date_projects = {
        date: {name: finalize_stats(stats) for name, stats in by_tokens(by_date_project[date].items())}
        for date in sorted(by_date_project, reverse=True)
    }

    # The grand cost only ever aggregates priced buckets, so it stays a real number.
    summary = add_display(grand)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_filter": date_filter,
        "summary": summary,
        "by_date": sorted_dates,
        "by_project": sorted_projects,
        "by_date_project": sorted_date_projects,
    }


def print_summary(report: dict):
    """Print a human-readable summary to stdout."""
    # Project names are paths; a redirected Windows stdout is cp1252 and would raise on most non-Latin names.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    s = report["summary"]
    print("=" * 60)
    print("  CLAUDE CODE TOKEN USAGE REPORT")
    print("=" * 60)

    if report["date_filter"]:
        print(f"  Filter: {report['date_filter']}")

    print(f"  Generated: {report['generated_at'][:19]}Z")
    print("-" * 60)
    print(f"  Total requests:    {s['requests']:>12,}")
    print(f"  Input tokens:      {s['input_tokens']:>12,}")
    print(f"  Output tokens:     {s['output_tokens']:>12,}")
    print(f"  Cache created:     {s['cache_creation_input_tokens']:>12,}")
    print(f"    - 5m write:      {s['cache_creation_5m_tokens']:>12,}")
    print(f"    - 1h write:      {s['cache_creation_1h_tokens']:>12,}")
    print(f"  Cache read:        {s['cache_read_input_tokens']:>12,}")
    print(f"  Total tokens:      {s['total_tokens']:>12,}")
    print("-" * 60)

    c = s["cost"]
    print("  COST (API-equivalent):")
    print(f"    Input:           {c['input']:>12,.2f} USD")
    print(f"    Output:          {c['output']:>12,.2f} USD")
    print(f"    Cache write 5m:  {c['cache_write_5m']:>12,.2f} USD")
    print(f"    Cache write 1h:  {c['cache_write_1h']:>12,.2f} USD")
    print(f"    Cache read:      {c['cache_read']:>12,.2f} USD")
    print(f"    Web search:      {c['web_search']:>12,.2f} USD")
    print(f"    TOTAL:           {c['total']:>12,.2f} USD")

    if s["unpriced_requests"]:
        print("-" * 60)
        print(f"  ! {s['unpriced_requests']:,} requests across {len(s['unpriced_models'])} model(s) "
              f"have no price table")
        print(f"    ({s['unpriced_tokens']:,} tokens counted, not billed): "
              f"{', '.join(s['unpriced_models'])}")
    print("-" * 60)

    # Top 5 dates
    dates = report["by_date"]
    if dates:
        print("\n  TOP DATES:")
        for i, (date, models) in enumerate(dates.items()):
            if i >= 7:
                break
            day_total = sum(total_tokens(m) for m in models.values())
            day_reqs = sum(m["requests"] for m in models.values())
            model_list = ", ".join(models.keys())
            print(f"    {date}: {day_total:>10,} tokens ({day_reqs:,} reqs) [{model_list}]")

    projects = report["by_project"]
    if projects:
        print("\n  TOP PROJECTS:")
        for i, (name, stats) in enumerate(projects.items()):
            if i >= 10:
                break
            cost = stats["cost"]["total"] if stats["cost"] else None
            cost_str = f"${cost:>10,.2f}" if cost is not None else "  unpriced"
            print(f"    {name:<40} {total_tokens(stats):>10,} tokens "
                  f"({stats['requests']:,} reqs) {cost_str}")

    print("=" * 60)


USAGE = """Usage: python3 track_tokens.py [today | yesterday | YYYY-MM-DD]
  No argument: all-time report. Days are this computer's local days.
  Prints a summary and writes token_usage.json next to this script."""


def main():
    date_filter = None
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        # Days are the user's local days, the same buckets the report uses.
        if arg in ("-h", "--help"):
            print(USAGE)
            sys.exit(0)
        if arg == "today":
            date_filter = datetime.now().astimezone().strftime("%Y-%m-%d")
        elif arg == "yesterday":
            date_filter = (datetime.now().astimezone() - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            try:
                date_filter = datetime.strptime(arg, "%Y-%m-%d").date().isoformat()
            except ValueError:
                print(f"Not a date: {arg!r}\n{USAGE}", file=sys.stderr)
                sys.exit(2)

    print("Scanning Claude Code transcripts...")
    try:
        all_entries, project_totals = collect_all_entries()
    except TranscriptsNotFound as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(all_entries):,} assistant responses across {len(project_totals)} projects\n")

    report = build_report(all_entries, project_totals, date_filter)

    # Save to JSON
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print_summary(report)
    print(f"\n  Report saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
