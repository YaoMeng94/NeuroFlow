#!/usr/bin/env python3
"""Run the existing BOLD BIDS App in a Nextflow task directory."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher-bundle-b64", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    try:
        bundle = json.loads(base64.urlsafe_b64decode(args.launcher_bundle_b64.encode()))
        command = bundle["command"]
        if not isinstance(command, list) or len(command) < 4 or command[0] != "bash":
            raise ValueError("invalid BOLD launcher command")
        if "--stage" not in command or command[command.index("--stage") + 1] != "all":
            raise ValueError("Nextflow pilot accepts only the full BOLD launcher")
        output_root = args.output_root.resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        command[3] = str(output_root)
        work_root = output_root / ".work" / "mefmriprep"
        if "--work-dir" in command:
            command[command.index("--work-dir") + 1] = str(work_root)
        else:
            command.extend(["--work-dir", str(work_root)])

        env = os.environ.copy()
        env.update(bundle.get("environment", {}))
        print("Running existing BOLD BIDS App as a Nextflow task:", flush=True)
        print(" ".join(command), flush=True)
        result = subprocess.run(command, env=env, check=False)
        if result.returncode:
            return result.returncode

        participant_dir = f"sub-{bundle['participant']}"
        for derivative in ("fmriprep", "tedana", "mefmriprep"):
            target = output_root / derivative / participant_dir
            if not target.is_dir():
                print(f"Expected BOLD output is missing: {target}", file=sys.stderr)
                return 1
        return 0
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Nextflow BOLD task setup failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
