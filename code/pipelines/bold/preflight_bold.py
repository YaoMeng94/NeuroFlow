#!/usr/bin/env python3
"""Metadata-driven preflight for BIDS BOLD echo groups."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import nibabel as nib


BOLD_RE = re.compile(r"^(?P<prefix>.+)_echo-(?P<echo>\d+)_bold\.nii(?:\.gz)?$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bids_dir", type=Path)
    parser.add_argument("--participant-label", nargs="+")
    parser.add_argument("--session-id", nargs="+")
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--allow-incomplete-echoes", action="store_true")
    return parser.parse_args()


def label_without_prefix(label: str) -> str:
    return label.removeprefix("sub-")


def sidecar(nii: Path) -> Path:
    name = nii.name[:-7] if nii.name.endswith(".nii.gz") else nii.name[:-4]
    return nii.with_name(name + ".json")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    args = parse_args()
    bids_dir = args.bids_dir.resolve()
    if not bids_dir.is_dir():
        print(f"ERROR: BIDS directory does not exist: {bids_dir}", file=sys.stderr)
        return 2

    requested = None
    if args.participant_label:
        requested = {label_without_prefix(label) for label in args.participant_label}
    requested_sessions = {label.removeprefix("ses-") for label in args.session_id or []}

    groups: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    warnings: list[str] = []

    for nii in sorted(bids_dir.glob("sub-*/**/func/*_bold.nii.gz")):
        match = BOLD_RE.match(nii.name)
        if not match:
            warnings.append(f"ignored non-echo BOLD filename: {nii.relative_to(bids_dir)}")
            continue
        subject = next(part[4:] for part in nii.parts if part.startswith("sub-"))
        if requested is not None and subject not in requested:
            continue
        if requested_sessions and not any(part.startswith("ses-") and part[4:] in requested_sessions for part in nii.parts):
            continue
        json_file = sidecar(nii)
        if not json_file.exists():
            fail(errors, f"missing sidecar: {json_file.relative_to(bids_dir)}")
            continue
        try:
            metadata = json.loads(json_file.read_text())
            image = nib.load(str(nii))
        except Exception as exc:
            fail(errors, f"cannot read {nii.relative_to(bids_dir)}: {exc}")
            continue

        prefix = match.group("prefix")
        groups.setdefault(prefix, []).append(
            {
                "echo": int(match.group("echo")),
                "path": str(nii.relative_to(bids_dir)),
                "json": str(json_file.relative_to(bids_dir)),
                "metadata": metadata,
                "shape": list(image.shape),
            }
        )

    if not groups:
        fail(errors, "no echo-specific *_bold.nii.gz files were found")

    manifest_groups: list[dict[str, Any]] = []
    for group_id, echoes in sorted(groups.items()):
        echoes.sort(key=lambda item: item["echo"])
        first = echoes[0]
        metadata = first["metadata"]
        echo_numbers = [item["echo"] for item in echoes]
        echo_times = [item["metadata"].get("EchoTime") for item in echoes]
        shapes = [item["shape"] for item in echoes]
        repetition_times = [item["metadata"].get("RepetitionTime") for item in echoes]
        phase_encoding = [item["metadata"].get("PhaseEncodingDirection") for item in echoes]
        slice_lengths = [len(item["metadata"].get("SliceTiming", [])) for item in echoes]

        if echo_numbers != list(range(1, len(echoes) + 1)):
            message = f"{group_id}: echo indices are not contiguous: {echo_numbers}"
            if args.allow_incomplete_echoes:
                warnings.append(message)
            else:
                fail(errors, message)
        if any(value is None for value in echo_times):
            fail(errors, f"{group_id}: every echo requires EchoTime")
        elif echo_times != sorted(echo_times) or len(set(echo_times)) != len(echo_times):
            fail(errors, f"{group_id}: EchoTime values are not strictly increasing: {echo_times}")
        if len(set(map(tuple, shapes))) != 1:
            fail(errors, f"{group_id}: echo image shapes differ: {shapes}")
        if len(set(repetition_times)) != 1:
            fail(errors, f"{group_id}: RepetitionTime differs across echoes: {repetition_times}")
        if len(set(phase_encoding)) != 1:
            fail(errors, f"{group_id}: PhaseEncodingDirection differs across echoes: {phase_encoding}")
        for echo, length in zip(echoes, slice_lengths):
            if "SliceTiming" not in echo["metadata"]:
                warnings.append(f"{group_id}: missing SliceTiming in echo-{echo['echo']}")
            elif length != echo["shape"][2]:
                fail(errors, f"{group_id}: SliceTiming length {length} does not match z-size {echo['shape'][2]}")
        if len(echoes) == 1:
            warnings.append(f"{group_id}: single echo; tedana will be skipped")

        subject_dir = bids_dir / next(part for part in first["path"].split("/") if part.startswith("sub-"))
        subject_relative_path = "/".join(first["path"].split("/")[1:])
        fmap_links = 0
        for fmap_json in sorted((subject_dir / "fmap").glob("*.json")):
            try:
                intended = json.loads(fmap_json.read_text()).get("IntendedFor", [])
            except Exception:
                continue
            fmap_links += int(any(subject_relative_path == value or value.endswith(subject_relative_path) for value in intended))
        if not fmap_links:
            warnings.append(f"{group_id}: no fmap IntendedFor link found for first echo")

        manifest_groups.append(
            {
                "group_id": group_id,
                "subject": f"sub-{subject}",
                "echoes": echo_numbers,
                "echo_times_seconds": echo_times,
                "repetition_time_seconds": repetition_times[0],
                "phase_encoding_direction": phase_encoding[0],
                "shape": shapes[0],
                "slice_timing_lengths": slice_lengths,
                "fmap_intendedfor_links": fmap_links,
                "files": [{key: value[key] for key in ("echo", "path", "json", "shape")} for value in echoes],
            }
        )

    manifest = {
        "SchemaVersion": "1.0.0",
        "BIDSRoot": str(bids_dir),
        "Participants": sorted({group["subject"] for group in manifest_groups}),
        "Groups": manifest_groups,
        "Warnings": warnings,
        "Errors": errors,
    }
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"BOLD groups: {len(manifest_groups)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Errors: {len(errors)}")
    if errors:
        for message in errors:
            print(f"ERROR: {message}", file=sys.stderr)
        return 1
    for message in warnings:
        print(f"WARNING: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
