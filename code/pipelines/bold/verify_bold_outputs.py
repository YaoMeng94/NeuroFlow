#!/usr/bin/env python3
"""Verify the reusable BOLD derivative contract after processing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import nibabel as nib
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bids-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--fmriprep-dir", type=Path)
    p.add_argument("--tedana-dir", type=Path)
    p.add_argument("--final-dir", type=Path)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--require-cifti", action="store_true")
    p.add_argument("--require-qc", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def finite(path: Path) -> bool:
    image = nib.load(str(path))
    data = np.asanyarray(image.dataobj)
    return bool(np.isfinite(data).all())


def files_for_groups(root: Path, pattern: str, group_ids: set[str]) -> list[Path]:
    """Return matching derivatives belonging to the current preflight manifest."""
    return sorted(
        path
        for path in root.rglob(pattern)
        if any(path.name.startswith(f"{group_id}_") for group_id in group_ids)
    )


def comparison_tsnr_errors(record: dict[str, object], tsnr_dir: Path) -> list[str]:
    group_id = str(record.get("GroupID", ""))
    baseline = record.get("ComparisonBaseline")
    if not isinstance(baseline, dict):
        return [f"{group_id}: comparison baseline is missing from QC metrics"]
    used_echo = baseline.get("UsedEcho")
    if not isinstance(used_echo, int) or used_echo < 1:
        return [f"{group_id}: invalid comparison echo in QC metrics"]

    map_path = tsnr_dir / f"{group_id}_echo-{used_echo}_desc-tsnr_bold.nii.gz"
    if not map_path.is_file():
        return [f"{group_id}: tSNR map for selected echo-{used_echo} is missing"]
    sidecar = map_path.with_suffix("").with_suffix(".json")
    try:
        metadata = json.loads(sidecar.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{group_id}: cannot read selected-echo tSNR sidecar: {exc}"]
    map_baseline = metadata.get("ComparisonBaseline", {})
    errors = []
    if map_baseline.get("UsedEcho") != used_echo:
        errors.append(f"{group_id}: selected-echo tSNR sidecar disagrees with QC metrics")
    source = Path(str(metadata.get("SourceImage", ""))).name
    if f"_echo-{used_echo}_" not in source:
        errors.append(f"{group_id}: selected-echo tSNR map source is not echo-{used_echo}")
    return errors


def main() -> int:
    config = parse_args()
    if config.dry_run:
        print("BOLD verification: DRY RUN")
        return 0
    payload = json.loads(config.manifest.read_text())
    group_ids = {group["group_id"] for group in payload.get("Groups", [])}
    subject_ids = {group_id.split("_", 1)[0] for group_id in group_ids}
    errors: list[str] = []
    checks: list[str] = []
    fmriprep = config.fmriprep_dir or config.output_dir / "fmriprep"
    tedana = config.tedana_dir or config.output_dir / "tedana"
    final_root = config.final_dir or config.output_dir / "mefmriprep"
    qc = final_root / "qc"

    if not (fmriprep / "dataset_description.json").exists():
        errors.append("missing fMRIPrep dataset_description.json")
    if not (tedana / "dataset_description.json").exists():
        errors.append("missing tedana dataset_description.json")
    reports = sorted(path for path in fmriprep.glob("sub-*.html") if path.stem in subject_ids)
    if reports:
        checks.append(f"fMRIPrep reports={len(reports)}")
    else:
        errors.append("no fMRIPrep HTML reports found")

    for group in payload.get("Groups", []):
        group_id = group["group_id"]
        candidates = sorted(tedana.rglob(f"{group_id}_desc-optcom_bold.nii.gz"))
        if len(group["echoes"]) > 1:
            if not candidates:
                errors.append(f"{group_id}: no tedana optimally-combined BOLD output")
                continue
            for path in candidates:
                if not finite(path):
                    errors.append(f"{group_id}: non-finite values in {path.name}")
            checks.append(f"{group_id}: optcom={len(candidates)}")
        else:
            checks.append(f"{group_id}: single-echo path")

    if config.require_cifti:
        cifti = files_for_groups(final_root, "*_space-fsLR_den-91k_desc-tedanaGSR_bold.dtseries.nii", group_ids)
        nogsr = files_for_groups(final_root, "*_space-fsLR_den-91k_desc-tedanaNoGSR_bold.dtseries.nii", group_ids)
        smooth = files_for_groups(final_root, "*_space-fsLR_den-91k_desc-tedanaGSRSmoothed_bold.dtseries.nii", group_ids)
        nogsr_smooth = files_for_groups(final_root, "*_space-fsLR_den-91k_desc-tedanaNoGSRSmoothed_bold.dtseries.nii", group_ids)
        volumes = files_for_groups(final_root, "*_space-T1w_desc-tedanaGSR_bold.nii.gz", group_ids)
        nogsr_volumes = files_for_groups(final_root, "*_space-T1w_desc-tedanaNoGSR_bold.nii.gz", group_ids)
        expected = len(payload.get("Groups", []))
        if len(cifti) != expected:
            errors.append("final default GSR CIFTI count does not match preflight groups")
        if len(nogsr) != expected:
            errors.append("final non-GSR CIFTI count does not match preflight groups")
        if len(smooth) != len(cifti):
            errors.append("smoothed final CIFTI count does not match unsmoothed CIFTI count")
        if len(volumes) != len(cifti):
            errors.append("final T1w NIfTI count does not match final CIFTI count")
        if len(nogsr_volumes) != len(nogsr):
            errors.append("final non-GSR T1w NIfTI count does not match non-GSR CIFTI count")
        if len(nogsr_smooth) != len(nogsr):
            errors.append("smoothed non-GSR CIFTI count does not match unsmoothed count")
        for path in cifti + nogsr + smooth + nogsr_smooth:
            if not finite(path):
                errors.append(f"non-finite values in final CIFTI: {path.name}")
            source_name = path.name.split("_space-fsLR_den-91k_desc-", 1)[0]
            source = sorted(tedana.rglob(f"{source_name}_desc-denoised_bold.nii.gz"))
            if len(source) != 1:
                errors.append(f"cannot resolve tedana source for {path.name}")
            elif nib.load(str(path)).shape[0] != nib.load(str(source[0])).shape[3]:
                errors.append(f"frame count differs from tedana source: {path.name}")
        if cifti:
            checks.append(f"final GSR CIFTI={len(cifti)}, non-GSR={len(nogsr)}, smoothed={len(smooth) + len(nogsr_smooth)}")

    if config.require_qc:
        if not (qc / "dataset_description.json").exists():
            errors.append("BOLD QC dataset_description.json is missing")
        metrics = qc / "bold_qc_metrics.json"
        if not metrics.exists():
            errors.append("BOLD QC metrics JSON is missing")
        else:
            try:
                qc_payload = json.loads(metrics.read_text())
                qc_records = [record for record in qc_payload.get("Records", []) if record.get("GroupID") in group_ids]
                if len(qc_records) != len(payload.get("Groups", [])):
                    errors.append("BOLD QC record count does not match preflight groups")
                for record in qc_records:
                    errors.extend(comparison_tsnr_errors(record, qc / "tsnr"))
                clean_masks = files_for_groups(qc / "clean_masks", "*_timeseries.tsv", group_ids)
                if not clean_masks:
                    errors.append("no FD-based clean-volume masks found")
                else:
                    for group_id in group_ids:
                        if not any(path.name.startswith(f"{group_id}_") for path in clean_masks):
                            errors.append(f"missing FD-based clean-volume mask for {group_id}")
                    checks.append(f"BOLD QC clean masks={len(clean_masks)}")
                tsnr_maps = files_for_groups(qc / "tsnr", "*.nii.gz", group_ids)
                if not tsnr_maps:
                    errors.append("no tSNR maps found")
                else:
                    for group_id in group_ids:
                        if not any(path.name.startswith(f"{group_id}_") for path in tsnr_maps):
                            errors.append(f"missing tSNR map for {group_id}")
                    for path in tsnr_maps:
                        if not finite(path):
                            errors.append(f"BOLD QC tSNR map contains non-finite values: {path.name}")
                        sidecar = path.with_suffix("").with_suffix(".json")
                        if not sidecar.exists():
                            errors.append(f"BOLD QC tSNR map JSON sidecar is missing: {sidecar.name}")
                    checks.append(f"BOLD QC tSNR maps={len(tsnr_maps)}")
                checks.append(f"BOLD QC records={len(qc_records)}")
            except (json.JSONDecodeError, OSError) as exc:
                errors.append(f"cannot read BOLD QC metrics: {exc}")

    for check in checks:
        print(f"CHECK: {check}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"BOLD verification: {'PASS' if not errors else 'FAIL'}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
