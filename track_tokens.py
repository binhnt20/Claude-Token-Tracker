#!/usr/bin/env python3
"""
Claude Code Token Usage Tracker (MVP)
Scans ~/.claude/projects/ transcripts and aggregates token usage into a JSON report.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pricing import cost_usd, normalize_model

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
    stats["cost"] = {
        "input": 0.0,
        "output": 0.0,
        "cache_write_5m": 0.0,
        "cache_write_1h": 0.0,
        "cache_read": 0.0,
        "total": 0.0,
    }
    # Counts, not a flag: a bucket keyed by project mixes models, so "is this unpriced?"
    # has no single answer. Counting lets a bucket be partially priced.
    stats["unpriced_requests"] = 0
    stats["unpriced_tokens"] = 0
    return stats


def entry_date(timestamp: str) -> str | None:
    """Extract YYYY-MM-DD from an ISO timestamp, or None when it cannot be parsed."""
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt.strftime("%Y-%m-%d")


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


def parse_transcript(filepath: Path) -> list[dict]:
    """Parse a JSONL transcript file into token usage entries, each carrying its own cost."""
    entries = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
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
                if not timestamp:
                    continue

                # Canonical name: snapshot suffixes price identically, so folding them here
                # keeps one series per model everywhere downstream. The key can be present
                # and null, which "unknown" as a get() default would not catch.
                model = normalize_model(message.get("model") or "unknown")
                session_id = entry.get("sessionId", "unknown")

                # Fast mode is billed at a different rate we do not model. Renaming the
                # model makes it miss the price table, so it reports as unpriced rather
                # than silently billing at standard rates.
                if usage.get("speed") == "fast":
                    model = f"{model} (fast)"

                cache_creation = usage.get("cache_creation")
                unmodelled_cache = False
                if isinstance(cache_creation, dict):
                    cache_5m = cache_creation.get("ephemeral_5m_input_tokens", 0)
                    cache_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
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
                    cache_write_total = usage.get("cache_creation_input_tokens", 0)
                    cache_5m = cache_write_total
                    cache_1h = 0

                record = {
                    "timestamp": timestamp,
                    "model": model,
                    "session_id": session_id,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": cache_write_total,
                    "cache_creation_5m_tokens": cache_5m,
                    "cache_creation_1h_tokens": cache_1h,
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                }

                date = entry_date(timestamp)
                if date and not unmodelled_cache:
                    record["cost"] = cost_usd(record, model, date)
                else:
                    record["cost"] = None
                entries.append(record)
    except (OSError, UnicodeDecodeError):
        pass
    return entries


def extract_project_name(dir_name: str) -> str:
    """Convert directory name like '-Users-binhnt-Coding-myproject' to 'myproject'."""
    parts = dir_name.replace("-", "/").strip("/").split("/")
    # Skip common path prefixes
    skip = {"Users", "home", "binhnt", "Coding"}
    meaningful = [p for p in parts if p and p not in skip]
    return "/".join(meaningful) if meaningful else dir_name


def aggregate(entries: list[dict], date_filter: str | None = None) -> dict:
    """Aggregate token usage by date and model."""
    by_date = defaultdict(lambda: defaultdict(new_stats))

    for e in entries:
        date_str = entry_date(e["timestamp"])
        if not date_str:
            continue
        if date_filter and date_str != date_filter:
            continue

        accumulate(by_date[date_str][e["model"]], e)

    return dict(by_date)


def collect_all_entries() -> tuple[list[dict], dict]:
    """Scan all projects and collect token usage entries."""
    all_entries = []
    project_totals = defaultdict(new_stats)

    if not CLAUDE_DIR.exists():
        print(f"Claude directory not found: {CLAUDE_DIR}")
        sys.exit(1)

    for project_dir in CLAUDE_DIR.iterdir():
        if not project_dir.is_dir():
            continue

        project_name = extract_project_name(project_dir.name)
        transcripts = list(project_dir.glob("*.jsonl"))
        transcripts += project_dir.glob("*/subagents/*.jsonl")

        for jsonl_file in transcripts:
            entries = parse_transcript(jsonl_file)
            all_entries.extend(entries)

            for e in entries:
                accumulate(project_totals[project_name], e)

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
    """Build the final report."""
    by_date = aggregate(all_entries, date_filter)

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

    # Sort projects by billable tokens descending, add display values
    sorted_projects = {}
    for name, stats in sorted(project_totals.items(), key=lambda x: total_tokens(x[1]), reverse=True):
        sorted_projects[name] = finalize_stats(stats)

    # The grand cost only ever aggregates priced buckets, so it stays a real number.
    summary = add_display(grand)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_filter": date_filter,
        "summary": summary,
        "by_date": sorted_dates,
        "by_project": sorted_projects,
    }


def print_summary(report: dict):
    """Print a human-readable summary to stdout."""
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

    # Top projects. These are always all-time: entries carry no project name, so the
    # per-project totals cannot be re-derived under a date filter.
    projects = report["by_project"]
    if projects:
        scope = " (all time — NOT filtered)" if report["date_filter"] else ""
        print(f"\n  TOP PROJECTS{scope}:")
        for i, (name, stats) in enumerate(projects.items()):
            if i >= 10:
                break
            cost = stats["cost"]["total"] if stats["cost"] else None
            cost_str = f"${cost:>10,.2f}" if cost is not None else "  unpriced"
            print(f"    {name:<40} {total_tokens(stats):>10,} tokens "
                  f"({stats['requests']:,} reqs) {cost_str}")

    print("=" * 60)


def main():
    date_filter = None
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "today":
            date_filter = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        elif arg == "yesterday":
            date_filter = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            date_filter = arg  # Expect YYYY-MM-DD

    print("Scanning Claude Code transcripts...")
    all_entries, project_totals = collect_all_entries()
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
