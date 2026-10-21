#!/usr/bin/env python3
"""Verify per-acquisition tensor and per-reconstruction NODDI derivatives."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import nibabel as nib
import numpy as np


DTI_PARAMETERS = ("fa", "md", "ad", "rd")
NODDI_PARAMETERS = ("icvf", "od", "isovf", "tf", "rmse", "nrmse")


def _group_key(path: Path) -> str:
    tokens = path.name.removesuffix(".nii.gz").split("_")
    entities = [
        token for token in tokens
        if token != "dwimap"
        and not token.startswith(("model-", "param-", "desc-"))
    ]
    return "_".join(entities)


def _dti_prefixes(qsiprep_dir: Path, subject: str) -> list[str]:
    subject_dir = qsiprep_dir / f"sub-{subject}"
    if not subject_dir.is_dir():
        raise ValueError(f"QSIPrep subject directory not found: {subject_dir}")
    suffix = "_desc-preproc_dwi.nii.gz"
    prefixes = sorted(
        path.name.removesuffix(suffix)
        for path in subject_dir.rglob(f"*{suffix}")
    )
    if not prefixes:
        raise ValueError(f"No preprocessed DWI acquisitions found under {subject_dir}")
    return prefixes


def _noddi_groups(root: Path, subject: str) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for path in root.rglob("*.nii.gz"):
        if f"sub-{subject}" not in path.name or "model-noddi" not in path.name:
            continue
        groups.setdefault(_group_key(path), []).append(path)
    return groups


def _matches(
    paths: list[Path], group: str, model: str, param: str, desc: str | None
) -> list[Path]:
    matches = []
    for path in paths:
        name = path.name
        if _group_key(path) != group or f"model-{model}" not in name or f"param-{param}" not in name:
            continue
        descriptions = [token.removeprefix("desc-") for token in name.split("_") if token.startswith("desc-")]
        if descriptions == ([desc] if desc else []):
            matches.append(path)
    return matches


def _validate_map(
    path: Path,
    model: str,
    param: str,
    desc: str | None,
    reference_geometry: tuple | None,
    errors: list[str],
) -> tuple[dict, tuple | None]:
    image = nib.load(str(path))
    data = np.asanyarray(image.dataobj)
    if data.ndim != 3:
        errors.append(f"{path.name}: expected 3D map, got {data.shape}")
    if not np.isfinite(data).all():
        errors.append(f"{path.name}: contains non-finite values")
    geometry = (image.shape, np.round(image.affine, 5).tolist())
    if reference_geometry is not None and geometry != reference_geometry:
        errors.append(f"{path.name}: geometry differs from other scalar maps")
    if reference_geometry is None:
        reference_geometry = geometry

    brain = data[data != 0]
    if model == "dti" and param == "fa" and brain.size:
        if float(brain.min()) < -1e-5 or float(brain.max()) > 1.00001:
            errors.append(f"{path.name}: FA values must be within [0, 1]")
    if model == "dti" and param in {"md", "ad", "rd"} and brain.size:
        if float(brain.min()) < -1e-10:
            errors.append(f"{path.name}: diffusivity values must be nonnegative")
    if model == "noddi" and param in {"icvf", "od", "isovf", "tf"} and brain.size:
        if float(brain.min()) < -1e-5 or float(brain.max()) > 1.00001:
            errors.append(f"{path.name}: expected values within [0, 1]")

    return (
        {
            "Model": model,
            "Parameter": param,
            "Description": desc,
            "Group": _group_key(path),
            "Path": str(path),
            "Shape": list(image.shape),
            "Minimum": float(brain.min()) if brain.size else 0.0,
            "Maximum": float(brain.max()) if brain.size else 0.0,
        },
        reference_geometry,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qsirecon-dir", type=Path, required=True)
    parser.add_argument("--qsiprep-dir", type=Path, required=True)
    parser.add_argument("--participant-label", required=True)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    subject = args.participant_label.removeprefix("sub-")
    errors: list[str] = []
    records: list[dict] = []
    reference_geometry = None

    try:
        dti_prefixes = _dti_prefixes(args.qsiprep_dir, subject)
    except ValueError as exc:
        dti_prefixes = []
        errors.append(str(exc))

    for prefix in dti_prefixes:
        group = _group_key(Path(prefix + "_model-dti_param-fa_dwimap.nii.gz"))
        for param in DTI_PARAMETERS:
            expected_name = f"{prefix}_model-dti_param-{param}_dwimap.nii.gz"
            matches = list(args.qsirecon_dir.rglob(expected_name))
            if len(matches) != 1:
                errors.append(
                    f"Expected one DTI {param} map for {prefix}, found {len(matches)}"
                )
                continue
            try:
                record, reference_geometry = _validate_map(
                    matches[0], "dti", param, None, reference_geometry, errors
                )
                record["Group"] = group
                records.append(record)
            except Exception as exc:
                errors.append(f"{matches[0].name}: cannot validate map: {exc}")

    noddi_groups = _noddi_groups(args.qsirecon_dir, subject)
    if not noddi_groups:
        errors.append(f"No NODDI maps found for sub-{subject}")
    noddi_requirements = [(param, None) for param in NODDI_PARAMETERS] + [
        ("icvf", "modulated"),
        ("od", "modulated"),
    ]
    all_noddi_paths = [path for paths in noddi_groups.values() for path in paths]
    for group in sorted(noddi_groups):
        for param, desc in noddi_requirements:
            matches = _matches(all_noddi_paths, group, "noddi", param, desc)
            if len(matches) != 1:
                label = f"desc-{desc} " if desc else ""
                errors.append(
                    f"Expected one NODDI {label}{param} map for {group}, found {len(matches)}"
                )
                continue
            try:
                record, reference_geometry = _validate_map(
                    matches[0], "noddi", param, desc, reference_geometry, errors
                )
                records.append(record)
            except Exception as exc:
                errors.append(f"{matches[0].name}: cannot validate map: {exc}")

    report = {
        "Subject": f"sub-{subject}",
        "DTIAcquisitions": dti_prefixes,
        "NODDIReconstructions": sorted(noddi_groups),
        "Records": records,
        "Errors": errors,
    }
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2) + "\n")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"DWI scalar verification: {'PASS' if not errors else 'FAIL'} ({len(records)} maps)")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
