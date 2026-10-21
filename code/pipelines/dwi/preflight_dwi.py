#!/usr/bin/env python3
"""Validate raw BIDS diffusion inputs and document reverse-PE b0 candidates."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np


DWI_RE = re.compile(r"^(?P<stem>.+)_dwi\.nii(?:\.gz)?$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bids_dir", type=Path)
    parser.add_argument("--participant-label", nargs="+")
    parser.add_argument("--session-id", nargs="+")
    parser.add_argument("--b0-threshold", type=float, default=50.0)
    parser.add_argument("--output-manifest", type=Path, required=True)
    return parser.parse_args()


def strip_sub(label: str) -> str:
    return label.removeprefix("sub-")


def strip_ses(label: str) -> str:
    return label.removeprefix("ses-")


def sidecar(path: Path) -> Path:
    stem = path.name[:-7] if path.name.endswith(".nii.gz") else path.name[:-4]
    return path.with_name(stem + ".json")


def opposite_pe(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return False
    first_axis, first_sign = first[0], -1 if first.endswith("-") else 1
    second_axis, second_sign = second[0], -1 if second.endswith("-") else 1
    return first_axis == second_axis and first_sign != second_sign


def relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def normalized_intended_target(value: str, subject: str, session: str | None) -> str:
    value = value.removeprefix("bids::").lstrip("/")
    entity_prefix = f"{subject}/"
    if session:
        entity_prefix += f"{session}/"
    return value.removeprefix(entity_prefix)


def fieldmap_targets_dwi(fieldmap: dict[str, Any], item: dict[str, Any]) -> bool:
    intended = fieldmap["intended_for"]
    if isinstance(intended, str):
        intended = [intended]
    target = normalized_intended_target(item["path"], item["subject"], item["session"])
    return any(
        normalized_intended_target(str(value), item["subject"], item["session"]) == target
        for value in intended
    )


def finite_array(values: list[float]) -> bool:
    return all(math.isfinite(value) for value in values)


def main() -> int:
    args = parse_args()
    root = args.bids_dir.resolve()
    if not root.is_dir():
        print(f"ERROR: BIDS directory does not exist: {root}", file=sys.stderr)
        return 2

    requested = None
    if args.participant_label:
        requested = {strip_sub(label) for label in args.participant_label}
    requested_sessions = None
    if args.session_id:
        requested_sessions = {strip_ses(label) for label in args.session_id}

    errors: list[str] = []
    warnings: list[str] = []
    dwi: list[dict[str, Any]] = []
    fieldmaps: list[dict[str, Any]] = []

    for path in sorted(root.glob("sub-*/**/dwi/*_dwi.nii.gz")):
        subject = next((part[4:] for part in path.parts if part.startswith("sub-")), "")
        session = next((part[4:] for part in path.parts if part.startswith("ses-")), "")
        if requested is not None and subject not in requested:
            continue
        if requested_sessions is not None and session not in requested_sessions:
            continue
        match = DWI_RE.match(path.name)
        if not match:
            continue
        json_path = sidecar(path)
        bval_path = path.with_name(path.name.replace(".nii.gz", ".bval"))
        bvec_path = path.with_name(path.name.replace(".nii.gz", ".bvec"))
        if not json_path.exists():
            errors.append(f"{relative(path, root)}: missing JSON sidecar")
            continue
        named_b0 = "b0" in path.name.lower()
        if (not bval_path.exists() or not bvec_path.exists()) and not named_b0:
            errors.append(f"{relative(path, root)}: DWI requires both bval and bvec sidecars")
            continue
        try:
            metadata = json.loads(json_path.read_text())
            image = nib.load(str(path))
            volumes = image.shape[3] if len(image.shape) == 4 else 1
            if bval_path.exists():
                bvals = [float(value) for value in bval_path.read_text().split()]
                if not bvec_path.exists() and not (named_b0 and all(value == 0 for value in bvals)):
                    raise ValueError("missing bvec sidecar for a nonzero DWI")
            elif named_b0:
                bvals = [0.0] * volumes
            else:
                raise ValueError("missing bval sidecar")
            if bvec_path.exists():
                bvec_rows = [
                    [float(value) for value in line.split()]
                    for line in bvec_path.read_text().splitlines()
                    if line.strip()
                ]
                if named_b0 and any(abs(value) > 1e-8 for row in bvec_rows for value in row):
                    raise ValueError("named b0-only DWI has nonzero b-vector values")
            elif named_b0:
                bvec_rows = [[0.0] * volumes for _ in range(3)]
                warnings.append(
                    f"{relative(path, root)}: named b0-only DWI is missing diffusion sidecar(s); it is eligible for TOPUP promotion"
                )
        except Exception as exc:
            errors.append(f"{relative(path, root)}: cannot read inputs: {exc}")
            continue

        shape = list(image.shape)
        volumes = shape[3] if len(shape) == 4 else 1
        if len(shape) != 4:
            errors.append(f"{relative(path, root)}: expected 4D DWI, got shape {shape}")
        if len(bvals) != volumes:
            errors.append(f"{relative(path, root)}: {len(bvals)} b-values for {volumes} volumes")
        if len(bvec_rows) != 3 or any(len(row) != volumes for row in bvec_rows):
            errors.append(f"{relative(path, root)}: bvec must be a 3 x {volumes} matrix")
        if not finite_array(bvals) or not all(finite_array(row) for row in bvec_rows):
            errors.append(f"{relative(path, root)}: bval/bvec contains non-finite values")

        b0_count = sum(value <= args.b0_threshold for value in bvals)
        is_b0_only = bool(bvals) and b0_count == len(bvals)
        slice_timing = metadata.get("SliceTiming")
        if slice_timing is not None and len(slice_timing) != shape[2]:
            errors.append(f"{relative(path, root)}: SliceTiming length {len(slice_timing)} != z-size {shape[2]}")

        dwi.append(
            {
                "subject": f"sub-{subject}",
                "session": f"ses-{session}" if session else None,
                "path": relative(path, root),
                "json": relative(json_path, root),
                "bval": relative(bval_path, root) if bval_path.exists() else None,
                "bvec": relative(bvec_path, root) if bvec_path.exists() else None,
                "stem": match.group("stem"),
                "shape": shape,
                "volumes": volumes,
                "b0_count": b0_count,
                "b0_threshold": args.b0_threshold,
                "is_b0_only": is_b0_only,
                "bmax": max(bvals) if bvals else None,
                "phase_encoding_direction": metadata.get("PhaseEncodingDirection"),
                "total_readout_time": metadata.get("TotalReadoutTime"),
                "effective_echo_spacing": metadata.get("EffectiveEchoSpacing"),
                "slice_timing_count": len(slice_timing) if slice_timing is not None else None,
                "has_b0_field_identifier": "B0FieldIdentifier" in metadata,
                "has_b0_field_source": "B0FieldSource" in metadata,
            }
        )

    for path in sorted(root.glob("sub-*/**/fmap/*_epi.nii.gz")):
        subject = next((part[4:] for part in path.parts if part.startswith("sub-")), "")
        session = next((part[4:] for part in path.parts if part.startswith("ses-")), "")
        if requested is not None and subject not in requested:
            continue
        if requested_sessions is not None and session not in requested_sessions:
            continue
        json_path = sidecar(path)
        if not json_path.exists():
            errors.append(f"{relative(path, root)}: missing fieldmap JSON sidecar")
            continue
        try:
            metadata = json.loads(json_path.read_text())
            image = nib.load(str(path))
        except Exception as exc:
            errors.append(f"{relative(path, root)}: cannot read fieldmap: {exc}")
            continue
        intended = metadata.get("IntendedFor", [])
        if isinstance(intended, str):
            intended = [intended]
        fieldmaps.append(
            {
                "subject": f"sub-{subject}",
                "session": f"ses-{session}" if session else None,
                "path": relative(path, root),
                "json": relative(json_path, root),
                "shape": list(image.shape),
                "phase_encoding_direction": metadata.get("PhaseEncodingDirection"),
                "total_readout_time": metadata.get("TotalReadoutTime"),
                "intended_for": intended,
            }
        )

    if not dwi:
        errors.append("no *_dwi.nii.gz files were found")

    for item in dwi:
        if not item["is_b0_only"]:
            continue
        item["role"] = "reverse-PE b0 candidate" if item["phase_encoding_direction"] else "b0-only DWI"

    for item in dwi:
        if item["is_b0_only"]:
            continue
        candidates = [
            candidate["path"]
            for candidate in dwi
            if candidate["is_b0_only"]
            and candidate["subject"] == item["subject"]
            and candidate["session"] == item["session"]
            and opposite_pe(item["phase_encoding_direction"], candidate["phase_encoding_direction"])
            and candidate["shape"][:3] == item["shape"][:3]
            and candidate["total_readout_time"] == item["total_readout_time"]
        ]
        item["opposite_pe_b0_candidates"] = candidates
        linked_epi = [
            fieldmap["path"]
            for fieldmap in fieldmaps
            if fieldmap["subject"] == item["subject"]
            and fieldmap["session"] == item["session"]
            and fieldmap["shape"][:3] == item["shape"][:3]
            and fieldmap["total_readout_time"] == item["total_readout_time"]
            and opposite_pe(item["phase_encoding_direction"], fieldmap["phase_encoding_direction"])
            and fieldmap_targets_dwi(fieldmap, item)
        ]
        item["opposite_pe_epi_fieldmaps"] = linked_epi
        if not candidates and not linked_epi:
            warnings.append(
                f"{item['path']}: no matching opposite-PE b0-only DWI or linked EPI fieldmap"
            )

    pe_validation: list[dict[str, Any]] = []
    for left in fieldmaps:
        for right in fieldmaps:
            if (
                left["path"] >= right["path"]
                or left["subject"] != right["subject"]
                or left["session"] != right["session"]
            ):
                continue
            if opposite_pe(left["phase_encoding_direction"], right["phase_encoding_direction"]):
                shared_targets = {
                    normalized_intended_target(str(value), left["subject"], left["session"])
                    for value in left["intended_for"]
                } & {
                    normalized_intended_target(str(value), right["subject"], right["session"])
                    for value in right["intended_for"]
                }
                if not shared_targets:
                    continue
                pe_validation.append(
                    {
                        "first": left["path"],
                        "second": right["path"],
                        "opposite_phase_encoding": True,
                        "same_total_readout_time": left["total_readout_time"] == right["total_readout_time"],
                    }
                )
                if left["total_readout_time"] != right["total_readout_time"]:
                    warnings.append(f"fieldmap pair has different TotalReadoutTime: {left['path']} / {right['path']}")

    manifest = {
        "SchemaVersion": "1.0.0",
        "BIDSRoot": str(root),
        "Participants": sorted({item["subject"] for item in dwi}),
        "Sessions": sorted({item["session"] for item in dwi if item["session"]}),
        "DWI": dwi,
        "Fieldmaps": fieldmaps,
        "PEValidation": pe_validation,
        "B0FilesAreSeparateFromSBRef": True,
        "Notes": [
            "A b0-only DWI series is distinct from *_sbref. Named b0 inputs may omit bval/bvec and are eligible for TOPUP promotion; sidecars are created only in a processing copy when QSIPrep requires them.",
            "Opposite-PE b0 candidates are recorded for QSIPrep selection; this preflight does not rewrite B0FieldSource metadata.",
        ],
        "Warnings": warnings,
        "Errors": errors,
    }
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"DWI series: {len(dwi)}")
    print(f"Fieldmap EPI series: {len(fieldmaps)}")
    print(f"Opposite-PE fieldmap pairs: {len(pe_validation)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Errors: {len(errors)}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
