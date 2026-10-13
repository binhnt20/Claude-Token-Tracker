#!/usr/bin/env python3
"""Where Claude Code keeps its transcripts: every projects directory, every JSONL file under it."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path


def warn_skipping(path: Path | str, exc: OSError) -> None:
    """One stderr line for a folder or file that cannot be read: the total leaves it out, so the user must know."""
    print(f"claude-token-tracker: skipping {path} ({exc.strerror or exc})", file=sys.stderr)


def _is_dir(path: Path) -> bool:
    """Like Path.is_dir(), but a folder that exists and cannot be reached is skipped with a warning, not raised."""
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        warn_skipping(path, exc)
        return False


def transcript_candidates(env: Mapping[str, str] | None = None, home: Path | None = None) -> list[Path]:
    """Every `projects` directory Claude Code may write to, whether it exists or not, in order.

    CLAUDE_CONFIG_DIR (a comma-separated list) replaces the defaults; otherwise both the legacy
    ~/.claude and the XDG ~/.config/claude locations are read, since installs have used each.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    config_dirs = [part.strip() for part in env.get("CLAUDE_CONFIG_DIR", "").split(",") if part.strip()]
    if config_dirs:
        return [Path(d).expanduser() / "projects" for d in config_dirs]
    return [home / ".claude" / "projects", home / ".config" / "claude" / "projects"]


def transcript_roots(env: Mapping[str, str] | None = None, home: Path | None = None) -> list[Path]:
    """Return the candidates that exist, without duplicates, in order."""
    roots, seen = [], set()
    for candidate in transcript_candidates(env, home):
        if not _is_dir(candidate):
            continue
        real = candidate.resolve()
        if real in seen:
            continue
        seen.add(real)
        roots.append(candidate)
    return roots


def iter_transcripts(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield (project folder name, path) for every *.jsonl at any depth under each project folder, sorted.

    A folder that cannot be listed is skipped with a warning; rglob would drop it without a word.
    """
    try:
        project_dirs = sorted(p for p in root.iterdir() if _is_dir(p))
    except OSError as exc:
        warn_skipping(root, exc)
        return
    for project_dir in project_dirs:
        paths = []
        for folder, _subdirs, files in os.walk(project_dir, onerror=lambda exc: warn_skipping(exc.filename, exc)):
            paths += [Path(folder) / name for name in files if name.endswith(".jsonl")]
        for path in sorted(paths):
            if path.is_file():
                yield project_dir.name, path
