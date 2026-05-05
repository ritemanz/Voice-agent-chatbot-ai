"""
One-shot setup for fresh clones.

Usage:
    python bootstrap.py

What it does:
    1. Creates a virtual environment at ./.venv (skips if it already exists).
    2. Upgrades pip inside that venv.
    3. Installs every dependency listed in requirements.txt.
    4. Prints the activation command and the run command for your platform.

This avoids shipping a per-machine virtualenv (whose pyvenv.cfg, Lib/ and
Scripts/ are tied to whoever created it) - every cloner gets a fresh,
correctly-pathed venv on their own machine.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQS = ROOT / "requirements.txt"


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _activation_hint(venv_dir: Path) -> str:
    rel = venv_dir.relative_to(ROOT)
    if os.name == "nt":
        return (
            f"  PowerShell:  .\\{rel}\\Scripts\\Activate.ps1\n"
            f"  cmd.exe:     {rel}\\Scripts\\activate.bat"
        )
    return f"  bash/zsh:    source {rel}/bin/activate"


def main() -> int:
    print(f"[bootstrap] Python {platform.python_version()} on {platform.system()}")
    print(f"[bootstrap] project root: {ROOT}")

    if VENV_DIR.exists():
        print(f"[bootstrap] venv already exists at {VENV_DIR} - reusing it.")
    else:
        print(f"[bootstrap] creating venv at {VENV_DIR} ...")
        venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(VENV_DIR)

    py = _venv_python(VENV_DIR)
    if not py.exists():
        print(f"[bootstrap] ERROR: expected interpreter not found at {py}", file=sys.stderr)
        return 1

    print("[bootstrap] upgrading pip ...")
    subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "pip"])

    if not REQS.exists():
        print(f"[bootstrap] ERROR: {REQS} not found", file=sys.stderr)
        return 1

    print(f"[bootstrap] installing dependencies from {REQS.name} ...")
    subprocess.check_call([str(py), "-m", "pip", "install", "-r", str(REQS)])

    print()
    print("[bootstrap] done.")
    print()
    print("Next steps:")
    print("  1. Copy .env.example to .env and fill in your API keys")
    print("     (see README section '2. Configure your API keys').")
    print("  2. Activate the virtual environment:")
    print(_activation_hint(VENV_DIR))
    print("  3. Start the server:")
    print("     python Lecture_9_Practice.py")
    print("     then open http://localhost:8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
