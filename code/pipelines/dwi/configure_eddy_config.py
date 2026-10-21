#!/usr/bin/env python3
"""Write a per-run CUDA Eddy config with the requested thread count."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--num-threads", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.num_threads <= 50:
        parser.error("--num-threads must be between 1 and 50")

    config = json.loads(args.source.read_text())
    if not isinstance(config, dict):
        parser.error("CUDA Eddy config must be a JSON object")
    config["num_threads"] = args.num_threads
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(json.dumps(config, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
