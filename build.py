#!/usr/bin/env python3
"""
Build standalone executable using PyInstaller.

Usage:
    python build.py          # Build for current platform
    python build.py --all    # Show instructions for all platforms
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

APP_NAME = "Claude Token Tracker"
EXE_NAME = "claude-token-tracker"
SCRIPT = "app.py"
ICON_MAC = "assets/icon.icns"
ICON_WIN = "assets/icon.ico"

ROOT = Path(__file__).parent


def run(cmd: list[str]):
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=ROOT)


def pyinstaller_base() -> list[str]:
    sep = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", EXE_NAME,
        f"--add-data=assets/chart.min.js{sep}assets",
        f"--add-data=assets/flatpickr.min.js{sep}assets",
        f"--add-data=assets/flatpickr.min.css{sep}assets",
        f"--add-data=assets/flatpickr-dark.css{sep}assets",
        f"--add-data=assets/icon.png{sep}assets",
        "--hidden-import=track_tokens",
        "--hidden-import=dashboard",
    ]
    return cmd


def build_macos():
    print("\n=== Building for macOS (.app + .dmg) ===\n")

    cmd = pyinstaller_base()
    if (ROOT / ICON_MAC).exists():
        cmd += ["--icon", ICON_MAC]
    cmd.append(SCRIPT)
    run(cmd)

    app_path = ROOT / "dist" / f"{EXE_NAME}.app"
    if app_path.exists():
        print(f"\n  .app bundle: {app_path}")

        # Create DMG if create-dmg is available
        dmg_path = ROOT / "dist" / f"{EXE_NAME}.dmg"
        try:
            run([
                "create-dmg",
                "--volname", APP_NAME,
                "--window-size", "600", "400",
                "--icon-size", "128",
                "--app-drop-link", "400", "200",
                "--icon", f"{EXE_NAME}.app", "200", "200",
                str(dmg_path),
                str(app_path),
            ])
            print(f"  .dmg: {dmg_path}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("  create-dmg not found. Install with: brew install create-dmg")
            print(f"  You can still distribute the .app bundle directly.")


def build_linux():
    print("\n=== Building for Linux (.AppImage) ===\n")

    cmd = pyinstaller_base()
    cmd.append(SCRIPT)
    run(cmd)

    exe_path = ROOT / "dist" / EXE_NAME
    if not exe_path.exists():
        print("  Build failed.")
        return

    # Create AppImage structure
    appdir = ROOT / "dist" / f"{APP_NAME}.AppDir"
    appdir.mkdir(parents=True, exist_ok=True)

    # AppRun
    apprun = appdir / "AppRun"
    apprun.write_text(f"""#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${{SELF%/*}}
exec "$HERE/usr/bin/{EXE_NAME}" "$@"
""")
    apprun.chmod(0o755)

    # Desktop entry
    (appdir / f"{EXE_NAME}.desktop").write_text(f"""[Desktop Entry]
Name={APP_NAME}
Exec={EXE_NAME}
Icon={EXE_NAME}
Type=Application
Categories=Utility;Development;
""")

    # Copy executable
    usr_bin = appdir / "usr" / "bin"
    usr_bin.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(exe_path, usr_bin / EXE_NAME)

    # Copy icon
    import shutil as _shutil
    icon_src = ROOT / "assets" / "icon-256.png"
    icon_dest = appdir / f"{EXE_NAME}.png"
    if icon_src.exists():
        _shutil.copy2(icon_src, icon_dest)
    elif not icon_dest.exists():
        import base64
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        icon_dest.write_bytes(png)

    # Build AppImage if appimagetool is available
    appimage_path = ROOT / "dist" / f"{EXE_NAME}-x86_64.AppImage"
    try:
        run(["appimagetool", str(appdir), str(appimage_path)])
        print(f"\n  .AppImage: {appimage_path}")
    except FileNotFoundError:
        print("\n  appimagetool not found. Install from: https://appimage.github.io/appimagetool/")
        print(f"  AppDir structure created at: {appdir}")
        print(f"  Standalone binary available at: {exe_path}")


def build_windows():
    print("\n=== Building for Windows (.exe) ===\n")

    cmd = pyinstaller_base()
    if (ROOT / ICON_WIN).exists():
        cmd += ["--icon", ICON_WIN]
    cmd.append(SCRIPT)
    run(cmd)

    exe_path = ROOT / "dist" / f"{EXE_NAME}.exe"
    if exe_path.exists():
        print(f"\n  .exe: {exe_path}")


def main():
    if "--all" in sys.argv:
        print("Build instructions for all platforms:")
        print("  macOS:  python build.py   (produces .app, optionally .dmg)")
        print("  Linux:  python build.py   (produces binary, optionally .AppImage)")
        print("  Windows: python build.py  (produces .exe)")
        print("\nPrerequisites:")
        print("  pip install pyinstaller pywebview")
        print("  macOS .dmg:     brew install create-dmg")
        print("  Linux .AppImage: install appimagetool")
        return

    os_name = platform.system()
    print(f"Platform: {os_name}")

    if os_name == "Darwin":
        build_macos()
    elif os_name == "Linux":
        build_linux()
    elif os_name == "Windows":
        build_windows()
    else:
        print(f"Unknown platform: {os_name}")
        sys.exit(1)

    print("\nDone!")


if __name__ == "__main__":
    main()
