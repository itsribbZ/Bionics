"""Bionics Build Script - Creates standalone .exe via PyInstaller.

Usage:
    python build.py          # Build the exe
    python build.py --run    # Build and run
    python build.py --clean  # Clean build artifacts
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "Bionics.spec"


def clean():
    """Remove build artifacts."""
    for d in [DIST, BUILD]:
        if d.exists():
            shutil.rmtree(d)
            print(f"Removed {d}")
    if SPEC.exists():
        SPEC.unlink()
        print(f"Removed {SPEC}")
    print("Clean complete")


def build():
    """Build standalone exe."""
    print("Building Bionics standalone...")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "Bionics",
        "--onedir",
        "--console",  # Console visible for debugging; use --windowed for release
        "--noconfirm",
        "--log-level", "WARN",
        # Include data files
        "--add-data", f"config.yaml{os.pathsep}.",
        "--add-data", f"gui/styles{os.pathsep}gui/styles",
        "--add-data", f"plans{os.pathsep}plans",
        "--add-data", f"core{os.pathsep}core",
        "--add-data", f"ue5_modules{os.pathsep}ue5_modules",
        "--add-data", f"bionics_tools{os.pathsep}bionics_tools",
        # Hidden imports that PyInstaller misses
        "--hidden-import", "anthropic",
        "--hidden-import", "bionics_tools",
        "--hidden-import", "bionics_tools.ue5_animgraph",
        "--hidden-import", "core.bridge",
        "--hidden-import", "core.agent",
        "--hidden-import", "core.auto_planner",
        "--hidden-import", "core.mvp_doctor",
        "--hidden-import", "core.ue5_bridge",
        "--hidden-import", "fastmcp",
        "--hidden-import", "pydantic",
        "--hidden-import", "mss",
        "--hidden-import", "cv2",
        "--hidden-import", "pyautogui",
        "--hidden-import", "pynput",
        "--hidden-import", "pynput.keyboard",
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse",
        "--hidden-import", "pynput.mouse._win32",
        "--hidden-import", "PIL",
        "--hidden-import", "fitz",
        "--hidden-import", "yaml",
        "--hidden-import", "numpy",
        "--hidden-import", "requests",
        "--hidden-import", "structuresim",
        # Entry point
        "main.py",
    ]

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("Build FAILED")
        sys.exit(1)

    exe_path = DIST / "Bionics" / "Bionics.exe"
    if exe_path.exists():
        print(f"\nBuild SUCCESS: {exe_path}")
        print(f"Size: {exe_path.stat().st_size / 1024 / 1024:.1f} MB")

        # Ensure audit and templates dirs exist in dist
        (DIST / "Bionics" / "audit").mkdir(exist_ok=True)
        (DIST / "Bionics" / "audit" / "sessions").mkdir(exist_ok=True)
        (DIST / "Bionics" / "templates" / "ui").mkdir(parents=True, exist_ok=True)

        print(f"\nRun with: {exe_path}")
    else:
        print("Build completed but exe not found")


def run():
    """Build and run."""
    build()
    exe_path = DIST / "Bionics" / "Bionics.exe"
    if exe_path.exists():
        print("\nLaunching Bionics...")
        subprocess.Popen([str(exe_path)])


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    elif "--run" in sys.argv:
        run()
    else:
        build()
