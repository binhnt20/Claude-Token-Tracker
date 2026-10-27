#!/usr/bin/env python3
"""Where Claude Code keeps its transcripts: every projects directory, every JSONL file under it."""

from __future__ import annotations

import ntpath
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

# The Windows share for the files of a WSL distribution; \\wsl$ is its name before Windows 11 and 10 21H2.
WSL_SHARES = ("\\\\wsl.localhost", "\\\\wsl$")
# WSL appends the Windows PATH, and this entry of it sits in the Windows user folder: /mnt/c/Users/<you>/AppData/...
_WINDOWS_APPS = re.compile(r"^(/.+/Users/[^/]+)/AppData/Local/Microsoft/WindowsApps/?$", re.IGNORECASE)


def data_dir(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """The tracker's folder in the user's data folder: a packaged app's report and the status line samples go here."""
    if platform == "win32":
        base = Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else home / "AppData" / "Local"
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(env["XDG_DATA_HOME"]) if env.get("XDG_DATA_HOME") else home / ".local" / "share"
    return base / "claude-token-tracker"


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


def windows_homes_on_path(path_var: str) -> list[Path]:
    """The Windows user folders named by WindowsApps entries of a WSL PATH, once each, in order."""
    homes = []
    for entry in path_var.split(":"):
        match = _WINDOWS_APPS.match(entry)
        if match and Path(match.group(1)) not in homes:
            homes.append(Path(match.group(1)))
    return homes


def wsl_exe(env: Mapping[str, str], exists: Callable[[str], bool] = os.path.exists) -> str:
    """The system's wsl.exe by full path: by bare name Windows runs one planted in the app's or the current folder.

    A 32-bit Python on 64-bit Windows sees System32 redirected to SysWOW64, which has no wsl.exe; Sysnative is the
    real System32 for it, and exists for no other process.
    """
    root = env.get("SystemRoot") or r"C:\Windows"
    sysnative = ntpath.join(root, "Sysnative", "wsl.exe")
    return sysnative if exists(sysnative) else ntpath.join(root, "System32", "wsl.exe")


def running_wsl_distros(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list[str]:
    """Names of the WSL distributions running now; none when WSL is absent or fails.

    `--running` never starts a stopped distribution. wsl.exe writes UTF-16 unless WSL_UTF8=1 is set.
    """
    try:
        result = run([wsl_exe(os.environ), "--list", "--running", "--quiet"], capture_output=True, timeout=10,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    out = result.stdout or b""
    text = out.decode("utf-16-le" if b"\0" in out else "utf-8", errors="replace").replace("\ufeff", "")
    return [line.strip() for line in text.splitlines() if line.strip()]


def wsl_distro_homes(distro: str, shares: Sequence[str] = WSL_SHARES) -> list[Path]:
    """The home folders inside one distribution, read through the first share name this Windows answers to."""
    for share in shares:
        try:
            return sorted(p for p in Path(share, distro, "home").iterdir() if _is_dir(p))
        except OSError:
            continue
    return []


def other_side_homes(env: Mapping[str, str], platform: str,
                     list_distros: Callable[[], list[str]] = running_wsl_distros,
                     shares: Sequence[str] = WSL_SHARES) -> list[Path]:
    """Home folders of the other system on a Windows + WSL machine, which keeps its own Claude Code history.

    In WSL: the Windows user folder. On Windows: the homes of every running distribution. The two sides never
    share an API call, so their sum counts nothing twice.
    """
    if platform.startswith("linux"):
        return windows_homes_on_path(env.get("PATH", ""))
    if platform == "win32":
        return [home for distro in list_distros() for home in wsl_distro_homes(distro, shares)]
    return []


def transcript_candidates(env: Mapping[str, str] | None = None, home: Path | None = None,
                          platform: str | None = None, other_homes: list[Path] | None = None) -> list[Path]:
    """Every `projects` directory Claude Code may write to, whether it exists or not, in order.

    CLAUDE_TOKEN_TRACKER_DIRS (a comma-separated list) replaces every default. CLAUDE_CONFIG_DIR (also a list) comes
    first and keeps the defaults: account switchers such as Orca set it in every shell, so it does not mean "only
    here". The defaults are the legacy ~/.claude and the XDG ~/.config/claude locations, since installs have used
    each, in this home and in the homes of the other side of a Windows + WSL machine.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    only = _dir_list(env.get("CLAUDE_TOKEN_TRACKER_DIRS", ""))
    if only:
        return [d / "projects" for d in only]
    if other_homes is None:
        other_homes = other_side_homes(env, sys.platform if platform is None else platform)
    defaults = [h / sub for h in [home, *other_homes] for sub in (Path(".claude"), Path(".config", "claude"))]
    return [d / "projects" for d in [*_dir_list(env.get("CLAUDE_CONFIG_DIR", "")), *defaults]]


def _dir_list(value: str) -> list[Path]:
    return [Path(part.strip()).expanduser() for part in value.split(",") if part.strip()]


def transcript_roots(env: Mapping[str, str] | None = None, home: Path | None = None,
                     platform: str | None = None, other_homes: list[Path] | None = None) -> list[Path]:
    """Return the candidates that exist, without duplicates, in order."""
    roots, seen = [], set()
    for candidate in transcript_candidates(env, home, platform, other_homes):
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
