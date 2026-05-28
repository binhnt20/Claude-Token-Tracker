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

CLAUDE_DIR = Path.home() / ".claude" / "projects"
OUTPUT_FILE = Path(__file__).parent / "token_usage.json"


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


def parse_transcript(filepath: Path) -> list[dict]:
    """Parse a JSONL transcript file and extract token usage entries."""
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
                if not usage:
                    continue

                timestamp = entry.get("timestamp")
                if not timestamp:
                    continue

                model = message.get("model", "unknown")
                session_id = entry.get("sessionId", "unknown")

                entries.append({
                    "timestamp": timestamp,
                    "model": model,
                    "session_id": session_id,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                })
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
    """Aggregate token usage by date, model, and project."""
    by_date = defaultdict(lambda: defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "requests": 0,
    }))

    for e in entries:
        try:
            dt = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue

        date_str = dt.strftime("%Y-%m-%d")
        if date_filter and date_str != date_filter:
            continue

        model = e["model"]
        stats = by_date[date_str][model]
        stats["input_tokens"] += e["input_tokens"]
        stats["output_tokens"] += e["output_tokens"]
        stats["cache_creation_input_tokens"] += e["cache_creation_input_tokens"]
        stats["cache_read_input_tokens"] += e["cache_read_input_tokens"]
        stats["requests"] += 1

    return dict(by_date)


def collect_all_entries() -> tuple[list[dict], dict]:
    """Scan all projects and collect token usage entries."""
    all_entries = []
    project_totals = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "requests": 0,
    })

    if not CLAUDE_DIR.exists():
        print(f"Claude directory not found: {CLAUDE_DIR}")
        sys.exit(1)

    for project_dir in CLAUDE_DIR.iterdir():
        if not project_dir.is_dir():
            continue

        project_name = extract_project_name(project_dir.name)

        for jsonl_file in project_dir.glob("*.jsonl"):
            entries = parse_transcript(jsonl_file)
            all_entries.extend(entries)

            for e in entries:
                stats = project_totals[project_name]
                stats["input_tokens"] += e["input_tokens"]
                stats["output_tokens"] += e["output_tokens"]
                stats["cache_creation_input_tokens"] += e["cache_creation_input_tokens"]
                stats["cache_read_input_tokens"] += e["cache_read_input_tokens"]
                stats["requests"] += 1

        # Also scan subagent transcripts
        for subagent_file in project_dir.glob("*/subagents/*.jsonl"):
            entries = parse_transcript(subagent_file)
            all_entries.extend(entries)

            for e in entries:
                stats = project_totals[project_name]
                stats["input_tokens"] += e["input_tokens"]
                stats["output_tokens"] += e["output_tokens"]
                stats["cache_creation_input_tokens"] += e["cache_creation_input_tokens"]
                stats["cache_read_input_tokens"] += e["cache_read_input_tokens"]
                stats["requests"] += 1

    return all_entries, dict(project_totals)


def build_report(all_entries: list[dict], project_totals: dict, date_filter: str | None = None) -> dict:
    """Build the final report."""
    by_date = aggregate(all_entries, date_filter)

    # Grand totals
    grand = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "requests": 0,
    }
    for date_models in by_date.values():
        for stats in date_models.values():
            for k in grand:
                grand[k] += stats[k]

    grand["total_tokens"] = grand["input_tokens"] + grand["output_tokens"]

    # Sort dates descending, add display values
    sorted_dates = {}
    for date in sorted(by_date.keys(), reverse=True):
        sorted_dates[date] = {model: add_display(s) for model, s in by_date[date].items()}

    # Sort projects by total tokens descending, add display values
    sorted_projects = {}
    for name, stats in sorted(
        project_totals.items(),
        key=lambda x: x[1]["input_tokens"] + x[1]["output_tokens"],
        reverse=True,
    ):
        sorted_projects[name] = add_display(stats)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_filter": date_filter,
        "summary": add_display(grand),
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
    print(f"  Cache read:        {s['cache_read_input_tokens']:>12,}")
    print(f"  Total tokens:      {s['total_tokens']:>12,}")
    print("-" * 60)

    # Top 5 dates
    dates = report["by_date"]
    if dates:
        print("\n  TOP DATES:")
        for i, (date, models) in enumerate(dates.items()):
            if i >= 7:
                break
            day_total = sum(m["input_tokens"] + m["output_tokens"] for m in models.values())
            day_reqs = sum(m["requests"] for m in models.values())
            model_list = ", ".join(models.keys())
            print(f"    {date}: {day_total:>10,} tokens ({day_reqs:,} reqs) [{model_list}]")

    # Top projects
    projects = report["by_project"]
    if projects:
        print("\n  TOP PROJECTS:")
        for i, (name, stats) in enumerate(projects.items()):
            if i >= 10:
                break
            total = stats["input_tokens"] + stats["output_tokens"]
            print(f"    {name:<40} {total:>10,} tokens ({stats['requests']:,} reqs)")

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
