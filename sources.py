#!/usr/bin/env python3
"""Where Claude Code keeps its transcripts: every projects directory, every JSONL file under it."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path


def transcript_roots(env: Mapping[str, str] | None = None, home: Path | None = None) -> list[Path]:
    """Return the existing `projects` directories Claude Code writes to, without duplicates, in order.

    CLAUDE_CONFIG_DIR (a comma-separated list) replaces the defaults; otherwise both the legacy
    ~/.claude and the XDG ~/.config/claude locations are read, since installs have used each.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    config_dirs = [part.strip() for part in env.get("CLAUDE_CONFIG_DIR", "").split(",") if part.strip()]
    if config_dirs:
        candidates = [Path(d).expanduser() / "projects" for d in config_dirs]
    else:
        candidates = [home / ".claude" / "projects", home / ".config" / "claude" / "projects"]

    roots, seen = [], set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        real = candidate.resolve()
        if real in seen:
            continue
        seen.add(real)
        roots.append(candidate)
    return roots


def iter_transcripts(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield (project folder name, path) for every *.jsonl at any depth under each project folder, sorted."""
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(project_dir.rglob("*.jsonl")):
            if path.is_file():
                yield project_dir.name, path
