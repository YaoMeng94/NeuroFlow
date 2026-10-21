#!/usr/bin/env python3
"""Select and audit a reconstruction from completed QSIPrep gradient tables."""

import argparse
import csv
import json
import math
from pathlib import Path

import nibabel as nib


def load_scan_metadata(path: Path) -> tuple[dict, str]:
    """Load QSIPrep sidecar metadata, or recover sources from confounds TSV."""
    stem = path.name.removesuffix(".nii.gz")
    metadata_path = path.with_name(stem + ".json")
    if metadata_path.is_file():
        return json.loads(metadata_path.read_text()), "json"

    dwi_prefix = stem.split("_space-")[0]
    confounds_path = path.with_name(dwi_prefix + "_desc-confounds_timeseries.tsv")
    if not confounds_path.is_file():
        raise FileNotFoundError(
            f"Missing QSIPrep metadata {metadata_path} and confounds {confounds_path}"
        )
    with confounds_path.open(newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames or "original_file" not in reader.fieldnames:
            raise ValueError(f"{confounds_path} has no original_file provenance column")
        sources = list(dict.fromkeys(
            row["original_file"] for row in reader if row.get("original_file")
        ))
    return {"Sources": sources, "ScanGrouping": {}}, "confounds_tsv"


def shell_counts(values, b0_threshold=50.0, tolerance=100.0):
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("b-values must be finite, nonnegative, and nonempty")
    groups = []
    for value in sorted(v for v in values if v > b0_threshold):
        if not groups or value - groups[-1][0] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [{"bvalue": 0, "volumes": sum(v <= b0_threshold for v in values)}] + [
        {"bvalue": sum(g) / len(g), "volumes": len(g)} for g in groups
    ]


def resolve_spec(requested, counts):
    if not counts or any(n == 0 for n in counts):
        raise ValueError("Reconstruction requires diffusion-weighted shells")
    if requested == "auto":
        kinds = {n > 1 for n in counts}
        if len(kinds) != 1:
            raise ValueError("Mixed single/multi-shell inputs: select participants/sessions separately")
        return ("mrtrix_multishell_msmt_ACT-hsvs" if True in kinds
                else "mrtrix_singleshell_ss3t_ACT-hsvs")
    if requested.startswith("mrtrix_singleshell_") and any(n > 1 for n in counts):
        raise ValueError("Single-shell preset would discard acquired shells; use auto or MSMT")
    if requested.startswith("mrtrix_multishell_") and any(n < 2 for n in counts):
        raise ValueError("MSMT preset requires at least two nonzero shells for every input")
    return requested


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qsiprep_dir", type=Path)
    parser.add_argument("--recon-spec", default="auto")
    parser.add_argument("--participant-label", nargs="*", default=[])
    parser.add_argument("--session-id", nargs="*", default=[])
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    subjects = {"sub-" + p.removeprefix("sub-") for p in args.participant_label}
    sessions = {"ses-" + p.removeprefix("ses-") for p in args.session_id}
    records = []
    for path in sorted(args.qsiprep_dir.glob("sub-*/**/*desc-preproc_dwi.nii.gz")):
        parts = path.relative_to(args.qsiprep_dir).parts
        if subjects and parts[0] not in subjects:
            continue
        if sessions and not sessions.intersection(parts):
            continue
        stem = path.name.removesuffix(".nii.gz")
        values = [float(v) for v in path.with_name(stem + ".bval").read_text().split()]
        shape = nib.load(path).shape
        if len(shape) != 4 or shape[3] != len(values):
            raise ValueError(f"Volume/bval mismatch: {path}")
        rows = [line.split() for line in path.with_name(stem + ".bvec").read_text().splitlines() if line.strip()]
        if len(rows) != 3 or any(len(row) != len(values) for row in rows):
            raise ValueError(f"Volume/bvec mismatch: {path}")
        metadata, metadata_source = load_scan_metadata(path)
        shells = shell_counts(values)
        if shells[0]["volumes"] == 0:
            raise ValueError(f"No b0 volumes: {path}")
        records.append({"Input": str(path), "Volumes": len(values), "Shells": shells,
                        "Sources": metadata.get("Sources", []),
                        "ScanGrouping": metadata.get("ScanGrouping", {}),
                        "MetadataSource": metadata_source})
    chosen = resolve_spec(args.recon_spec, [len(r["Shells"]) - 1 for r in records])
    report = {"RequestedReconSpec": args.recon_spec, "ResolvedReconSpec": chosen,
              "B0Threshold": 50, "ShellTolerance": 100, "Inputs": records}
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(report, indent=2) + "\n")
    print(chosen)


if __name__ == "__main__":
    main()
