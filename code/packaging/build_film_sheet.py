"""Build the MRI film sheet desktop app for the current operating system."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = ROOT / "code" / "packaging" / "run_film_sheet.py"


def main() -> int:
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "NeuroFlowMRIFilmSheet",
        "--paths", str(ROOT / "code"),
        "--workpath", str(ROOT / "build" / "film_sheet"),
        "--distpath", str(ROOT / "dist" / "film_sheet"),
        "--collect-all", "pydicom",
        "--collect-all", "nibabel",
        "--collect-all", "scipy",
        "--collect-all", "reportlab",
        "--collect-all", "PIL",
        str(ENTRY_POINT),
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
