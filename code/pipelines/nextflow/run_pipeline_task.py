#!/usr/bin/env python3
"""Run an existing NeuroFlow BIDS App inside one Nextflow task."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path


def _run_pipeline(command: list[str], env: dict[str, str]) -> int:
    live_log = env.get("NEUROFLOW_TASK_LOG")
    if not live_log:
        return subprocess.run(command, env=env, check=False).returncode

    with subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    ) as process, Path(live_log).open("a", encoding="utf-8", buffering=1) as log:
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
        return process.wait()


def _bind_bids_input(command: list[str], bids_dir: Path) -> None:
    bids_source = bids_dir.resolve(strict=True)
    if not bids_source.is_dir() or Path(command[2]).resolve(strict=True) != bids_source:
        raise ValueError("tracked BIDS input does not match the launcher input directory")
    command[2] = str(bids_source)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher-bundle-b64", required=True)
    parser.add_argument("--bids-dir", required=True, type=Path)
    parser.add_argument("--bids-fingerprint", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--expected-derivatives", required=True)
    args = parser.parse_args()

    try:
        raw_bundle = base64.urlsafe_b64decode(args.launcher_bundle_b64.encode())
        bundle = json.loads(raw_bundle)
        command = bundle["command"]
        if not isinstance(command, list) or len(command) < 4 or command[0] != "bash":
            raise ValueError("invalid pipeline launcher command")
        _bind_bids_input(command, args.bids_dir)
        if len(args.bids_fingerprint) != 64 or any(char not in "0123456789abcdef" for char in args.bids_fingerprint):
            raise ValueError("invalid BIDS input metadata fingerprint")
        if "--stage" not in command or command[command.index("--stage") + 1] != "all":
            raise ValueError("Nextflow accepts only a complete modality workflow")

        output_root = args.output_root.resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        command[3] = str(output_root)
        work_root = output_root / ".work" / bundle["workflow"]
        if "--work-dir" in command:
            command[command.index("--work-dir") + 1] = str(work_root)
        else:
            command.extend(["--work-dir", str(work_root)])

        env = os.environ.copy()
        env.update(bundle.get("environment", {}))
        print("Running existing BIDS App as a Nextflow task:", flush=True)
        print(f"BIDS metadata fingerprint: {args.bids_fingerprint}", flush=True)
        print(" ".join(command), flush=True)
        return_code = _run_pipeline(command, env)
        if return_code:
            return return_code

        participant_dir = f"sub-{bundle['participant']}"
        for derivative in args.expected_derivatives.split(","):
            subject_layout = Path("subjects") / participant_dir if derivative == "freesurfer" else Path(participant_dir)
            target = output_root / derivative / subject_layout
            if not target.is_dir():
                print(f"Expected {bundle['workflow']} output is missing: {target}", file=sys.stderr)
                return 1
        return 0
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Nextflow task setup failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
