#!/usr/bin/env python3
"""Run standalone tedana on fMRIPrep-corrected multi-echo BOLD data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fmriprep-dir", type=Path, required=True)
    p.add_argument("--tedana-dir", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--work-dir", type=Path, required=True)
    backend = p.add_mutually_exclusive_group(required=True)
    backend.add_argument("--tedana-image", type=Path)
    backend.add_argument("--tedana-command", help="native tedana executable or command")
    p.add_argument("--apptainer", default="apptainer")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--input-space", default="native")
    p.add_argument("--fittype", choices=("loglin", "curvefit"), default="loglin")
    p.add_argument("--tedpca", default="aic")
    p.add_argument("--ica-method", choices=("fastica", "robustica"), default="fastica")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def command_text(command: list[str]) -> str:
    return " ".join(shlex.quote(item) for item in command)


def find_echoes(root: Path, group_id: str, echo_count: int, space: str) -> list[Path]:
    candidates = []
    pattern = re.compile(rf"^{re.escape(group_id)}_echo-(\d+)(?P<rest>.*)_desc-preproc_bold\.nii\.gz$")
    for path in sorted(root.rglob("*.nii.gz")):
        match = pattern.match(path.name)
        if not match:
            continue
        rest = match.group("rest")
        if space == "native":
            if "_space-" in rest:
                continue
        elif space == "T1w":
            if "_space-T1w" not in rest:
                continue
        elif f"_space-{space}" not in rest:
            continue
        candidates.append((int(match.group(1)), path))
    by_echo = {echo: path for echo, path in candidates}
    missing = [echo for echo in range(1, echo_count + 1) if echo not in by_echo]
    if missing:
        raise FileNotFoundError(
            f"{group_id}: fMRIPrep corrected echoes missing for space {space}: {missing}"
        )
    return [by_echo[echo] for echo in range(1, echo_count + 1)]


def find_mask(root: Path, group_id: str, space: str) -> Path | None:
    candidates = sorted(root.rglob(f"{group_id}*desc-brain_mask.nii.gz"))
    for path in candidates:
        if space == "native" and "_space-" not in path.name:
            return path
        if space == "T1w" and "_space-T1w" in path.name:
            return path
        if space != "T1w" and f"_space-{space}" in path.name:
            return path
    return None


def write_dataset_description(output: Path, provenance: dict[str, str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    description = {
        "Name": "Prisma BOLD tedana derivatives",
        "BIDSVersion": "1.8.0",
        "DatasetType": "derivative",
        "GeneratedBy": [
            {
                "Name": "tedana",
                "Description": "Standalone TE-dependent multi-echo processing",
                **provenance,
            },
            {"Name": "Prisma BOLD pipeline", "Version": "0.1.0"},
        ],
    }
    (output / "dataset_description.json").write_text(json.dumps(description, indent=2) + "\n")


def main() -> int:
    config = args()
    if config.threads < 1:
        raise SystemExit("--threads must be positive")
    payload = json.loads(config.manifest.read_text())
    config.tedana_dir.mkdir(parents=True, exist_ok=True)
    config.work_dir.mkdir(parents=True, exist_ok=True)
    if config.tedana_command:
        resolved = shutil.which(config.tedana_command) or (
            str(Path(config.tedana_command).resolve()) if Path(config.tedana_command).exists() else None
        )
        if resolved is None and not config.dry_run:
            raise FileNotFoundError(f"tedana command not found: {config.tedana_command}")
        provenance = {"Command": config.tedana_command}
    else:
        if config.tedana_image is None:
            raise SystemExit("one of --tedana-image or --tedana-command is required")
        provenance = {"Container": str(config.tedana_image)}
    write_dataset_description(config.tedana_dir, provenance)
    records: list[dict[str, Any]] = []

    for group in payload.get("Groups", []):
        group_id = group["group_id"]
        echo_count = len(group["echoes"])
        record: dict[str, Any] = {
            "group_id": group_id,
            "subject": group["subject"],
            "echo_times_seconds": group["echo_times_seconds"],
            "input_space": config.input_space,
            "status": "pending",
        }
        if echo_count == 1:
            record["status"] = "skipped_single_echo"
            records.append(record)
            continue

        try:
            echoes = find_echoes(config.fmriprep_dir, group_id, echo_count, config.input_space)
        except FileNotFoundError:
            if config.dry_run:
                print(f"PLAN: tedana will process {group_id} after fMRIPrep creates corrected echoes")
                record["status"] = "planned_after_fmriprep"
                records.append(record)
                continue
            raise
        subject_dir = config.tedana_dir / group["subject"] / "func"
        subject_dir.mkdir(parents=True, exist_ok=True)
        group_work = config.work_dir / group_id
        group_work.mkdir(parents=True, exist_ok=True)
        existing = sorted(subject_dir.glob(f"{group_id}*optcom*bold*.nii.gz"))
        if existing and not config.overwrite:
            record.update(status="already_present", outputs=[str(path) for path in existing])
            records.append(record)
            continue

        common = [
            "-d",
            *[str(path) for path in echoes],
            "-e",
            *[str(value) for value in group["echo_times_seconds"]],
            "--out-dir",
            str(subject_dir),
            "--prefix",
            group_id,
            "--convention",
            "bids",
            "--fittype",
            config.fittype,
            "--combmode",
            "t2s",
            "--tedpca",
            config.tedpca,
            "--ica-method",
            config.ica_method,
            "--seed",
            "42",
            "--n-threads",
            str(config.threads),
            "--verbose",
        ]
        if config.tedana_command:
            command = [config.tedana_command, *common]
        else:
            container_common = common.copy()
            container_common[1 : 1 + len(echoes)] = [
                f"/fmriprep/{path.relative_to(config.fmriprep_dir)}" for path in echoes
            ]
            container_common[container_common.index(str(subject_dir))] = f"/out/{group['subject']}/func"
            command = [
                config.apptainer,
                "exec",
                "--cleanenv",
                "-B",
                f"{config.fmriprep_dir}:/fmriprep:ro",
                "-B",
                f"{config.tedana_dir}:/out",
                "-B",
                f"{config.work_dir}:/work",
                str(config.tedana_image),
                "tedana",
                *container_common,
            ]
        mask = find_mask(config.fmriprep_dir, group_id, config.input_space)
        if mask is not None:
            mask_path = str(mask) if config.tedana_command else f"/fmriprep/{mask.relative_to(config.fmriprep_dir)}"
            command.extend(["--mask", mask_path])
            record["mask"] = str(mask)
        record["command"] = command_text(command)
        if config.dry_run:
            print(record["command"])
            record["status"] = "dry_run"
        else:
            env = os.environ.copy()
            env["TMPDIR"] = str(group_work)
            subprocess.run(command, check=True, env=env)
            outputs = sorted(subject_dir.glob(f"{group_id}*"))
            record["outputs"] = [str(path) for path in outputs if path.is_file()]
            record["status"] = "complete"
        records.append(record)

    manifest = {
        "SchemaVersion": "1.0.0",
        "GeneratedAt": datetime.now(timezone.utc).isoformat(),
        "TedanaImage": str(config.tedana_image) if config.tedana_image else None,
        "TedanaCommand": config.tedana_command,
        "FMRIPrepDirectory": str(config.fmriprep_dir),
        "Records": records,
    }
    result_path = config.tedana_dir / "tedana_manifest.json"
    result_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote tedana manifest: {result_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
