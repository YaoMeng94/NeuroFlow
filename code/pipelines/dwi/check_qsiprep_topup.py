#!/usr/bin/env python3
"""Gate SCJ reconstruction on completed QSIPrep DWI and TOPUP provenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from verify_dwi_outputs import check_processing_overlay, check_topup_provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--expected-fmap", required=True)
    args = parser.parse_args()
    root = args.output_root.resolve()
    work = root / ".work/dwiprep"
    overlay = work / "dwi_processing_overlay.json"
    errors: list[str] = []
    checks: list[str] = []
    if not overlay.is_file():
        errors.append(f"Missing processing overlay: {overlay}")
    else:
        check_processing_overlay(overlay, errors, checks)
        record = json.loads(overlay.read_text())
        fmap = Path(record["ProcessingBIDS"]) / args.expected_fmap
        if not fmap.is_file():
            errors.append(f"Expected TOPUP fieldmap is missing: {fmap}")
        else:
            meta = json.loads(fmap.with_name(fmap.name.replace(".nii.gz", ".json")).read_text())
            if not meta.get("IntendedFor"):
                errors.append(f"Expected TOPUP fieldmap has no IntendedFor: {fmap}")
            else:
                checks.append(f"Expected EPI fieldmap links {len(meta['IntendedFor'])} DWI run(s)")
    check_topup_provenance(work / "qsiprep", overlay if overlay.is_file() else None, errors, checks, True)
    expected_origin = "/bids/" + args.expected_fmap.lstrip("/")
    selected = set()
    for table in (work / "qsiprep").glob("**/hmc_sdc_wf/gather_inputs/b0_selection_info.tsv"):
        rows = table.read_text().splitlines()
        if not rows:
            continue
        header = rows[0].split("\t")
        if "bids_origin_file" not in header or "selected_for_sdc" not in header:
            continue
        origin = header.index("bids_origin_file")
        is_selected = header.index("selected_for_sdc")
        for line in rows[1:]:
            fields = line.split("\t")
            if len(fields) > max(origin, is_selected) and fields[is_selected].lower() == "true":
                selected.add(fields[origin])
    if expected_origin not in selected:
        errors.append(f"TOPUP selection does not include {expected_origin}")
    preproc = list((root / "qsiprep/sub-SCJ/dwi").glob("*preproc_dwi.nii.gz"))
    if not preproc:
        errors.append("No completed preprocessed DWI for sub-SCJ")
    else:
        checks.append(f"Preprocessed DWI files: {len(preproc)}")
    for message in checks:
        print("PASS:", message)
    for message in errors:
        print("ERROR:", message)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
