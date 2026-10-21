"""Detached job subprocess entry point."""

import sys
from pathlib import Path

from .jobs import run_worker


if __name__ == "__main__":
    raise SystemExit(run_worker(Path(sys.argv[1])))
