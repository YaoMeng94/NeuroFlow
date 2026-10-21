#!/usr/bin/env python3
"""Check the reusable QSIPrep/QSIRecon derivative contract."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sys
from pathlib import Path

import nibabel as nib
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--participant-label", help="verify only derivatives for this participant")
    parser.add_argument("--output-json", type=Path, help="write a subject-scoped verification report")
    parser.add_argument(
        "--processing-overlay",
        type=Path,
        help="optional prepare_dwi_input overlay to validate the temporary BIDS copy",
    )
    parser.add_argument(
        "--expected-atlas",
        nargs="+",
        default=[],
        help="atlas names that must occur in QSIRecon connectivity output paths",
    )
    parser.add_argument(
        "--qsiprep-work-dir",
        type=Path,
        help="QSIPrep work directory to inspect for TOPUP provenance",
    )
    parser.add_argument(
        "--require-topup-provenance",
        action="store_true",
        help="require completed TOPUP selection/output evidence when a work directory is supplied",
    )
    return parser.parse_args()


def finite(path: Path) -> bool:
    image = nib.load(str(path))
    return bool(np.isfinite(np.asanyarray(image.dataobj)).all())


def check_dwi(path: Path, errors: list[str], checks: list[str]) -> None:
    try:
        image = nib.load(str(path))
        shape = image.shape
        if len(shape) != 4:
            errors.append(f"{path}: expected 4D NIfTI, got {shape}")
        elif not finite(path):
            errors.append(f"{path}: contains non-finite values")
        bval = path.with_name(path.name.replace(".nii.gz", ".bval"))
        bvec = path.with_name(path.name.replace(".nii.gz", ".bvec"))
        if bval.exists() != bvec.exists():
            missing = ".bval" if not bval.exists() else ".bvec"
            errors.append(f"{path}: missing diffusion sidecar {missing}")
        elif not bval.exists() and len(shape) == 4:
            errors.append(f"{path}: missing diffusion sidecars .bval and .bvec")
        elif len(shape) == 4:
            bvals = bval.read_text().split()
            rows = [line.split() for line in bvec.read_text().splitlines() if line.strip()]
            if len(bvals) != shape[3]:
                errors.append(f"{path}: bval count {len(bvals)} != volume count {shape[3]}")
            if len(rows) != 3 or any(len(row) != shape[3] for row in rows):
                errors.append(f"{path}: bvec dimensions do not match {shape[3]} volumes")
        checks.append(f"{path.relative_to(path.parents[3] if len(path.parents) > 3 else path.parent)} shape={shape}")
    except Exception as exc:
        errors.append(f"{path}: cannot inspect NIfTI: {exc}")


def check_connectivity_table(path: Path, errors: list[str], checks: list[str]) -> None:
    """Require a non-empty native QSIRecon connectivity artifact."""
    try:
        if path.stat().st_size == 0:
            errors.append(f"{path}: connectivity artifact is empty")
            return
        if path.suffix.lower() in {".csv", ".tsv"}:
            delimiter = "," if path.suffix.lower() == ".csv" else "\t"
            rows = []
            with path.open(newline="") as handle:
                for row in csv.reader(handle, delimiter=delimiter):
                    if any(cell.strip() for cell in row):
                        rows.append(row)
            if len(rows) < 2:
                errors.append(f"{path}: connectivity table has fewer than two non-empty rows")
                return
            numeric_cells = 0
            for row in rows:
                for cell in row:
                    try:
                        if math.isfinite(float(cell.strip())):
                            numeric_cells += 1
                    except (TypeError, ValueError):
                        continue
            if numeric_cells < 4:
                errors.append(f"{path}: connectivity table has fewer than four numeric cells")
                return
        elif path.suffix.lower() == ".mat" and path.stat().st_size < 1024:
            errors.append(f"{path}: MATLAB connectivity artifact is implausibly small")
            return
        checks.append(f"Connectivity artifact non-empty={path.name}")
    except Exception as exc:
        errors.append(f"{path}: cannot inspect connectivity artifact: {exc}")


def check_tractogram(path: Path, errors: list[str], checks: list[str]) -> None:
    """Catch a placeholder or zero-streamline tractogram."""
    try:
        if path.stat().st_size < 512:
            errors.append(f"{path}: tractogram is implausibly small")
            return
        if path.name.lower().endswith((".tck", ".tck.gz")):
            opener = gzip.open if path.name.lower().endswith(".gz") else open
            with opener(path, "rb") as handle:
                header = handle.read(8192).decode("ascii", errors="ignore")
            for line in header.splitlines():
                if line.lower().startswith("count:"):
                    count = line.split(":", 1)[1].strip()
                    if count.isdigit() and int(count) == 0:
                        errors.append(f"{path}: tractogram reports zero streamlines")
                        return
                    break
        checks.append(f"Tractogram artifact non-empty={path.name}")
    except Exception as exc:
        errors.append(f"{path}: cannot inspect tractogram: {exc}")


def connectivity_atlas_names(path: Path) -> set[str]:
    """Read atlas names from native QSIRecon MAT keys when available."""
    names: set[str] = set()
    if path.suffix.lower() == ".mat":
        try:
            from scipy.io import whosmat

            for variable, _shape, _dtype in whosmat(str(path)):
                if variable.startswith("atlas_"):
                    names.add(variable[len("atlas_") :].split("_", 1)[0])
        except Exception:
            pass
    for part in path.parts:
        if part.lower().startswith("atlas-"):
            names.add(part[6:])
    return names


def opposite_pe(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return False
    return first[0] == second[0] and first.endswith("-") != second.endswith("-")


def topup_pe_vector(row: list[str]) -> tuple[int, int, int] | None:
    """Parse the three signed PE components from one TOPUP datain row."""
    if len(row) < 4:
        return None
    try:
        raw_values = tuple(float(value) for value in row[:3])
    except ValueError:
        return None
    if any(value not in {-1.0, 0.0, 1.0} for value in raw_values) or sum(abs(value) for value in raw_values) != 1:
        return None
    return tuple(int(value) for value in raw_values)


def sidecar(path: Path, suffix: str) -> Path:
    return path.with_name(path.name.replace(".nii.gz", suffix))


def check_processing_overlay(path: Path, errors: list[str], checks: list[str]) -> None:
    try:
        overlay = json.loads(path.read_text())
        processing_root = Path(overlay["ProcessingBIDS"])
        promoted = overlay.get("DWIReversePEB0Fieldmaps", [])
        sources = overlay.get("DWIReversePEB0Sources", [])
        source_root = Path(overlay.get("SourceBIDS", ""))
        if sources and len(sources) != len(promoted):
            errors.append("overlay reverse-PE b0 source and promoted-fmap counts differ")
        for relative in promoted:
            fmap = processing_root / relative
            fmap_json = sidecar(fmap, ".json")
            if not fmap.exists() or not fmap_json.exists():
                errors.append(f"missing promoted reverse-PE b0 fieldmap: {fmap}")
                continue
            metadata = json.loads(fmap_json.read_text())
            if not fmap.name.endswith("_epi.nii.gz"):
                errors.append(f"promoted reverse-PE b0 is not an EPI fmap: {fmap}")
            if fmap.with_name(fmap.name.replace(".nii.gz", ".bval")).exists() or fmap.with_name(
                fmap.name.replace(".nii.gz", ".bvec")
            ).exists():
                errors.append(f"promoted reverse-PE b0 has diffusion sidecars: {fmap}")
            if not metadata.get("IntendedFor"):
                errors.append(f"promoted reverse-PE b0 has empty IntendedFor: {fmap_json}")
            if metadata.get("PhaseEncodingDirection") not in {"i", "i-", "j", "j-", "k", "k-"}:
                errors.append(f"promoted reverse-PE b0 has invalid PhaseEncodingDirection: {fmap_json}")
            readout = metadata.get("TotalReadoutTime")
            if not isinstance(readout, (int, float)) or not math.isfinite(float(readout)) or float(readout) <= 0:
                errors.append(f"promoted reverse-PE b0 has invalid TotalReadoutTime: {fmap_json}")
            for target in metadata.get("IntendedFor", []):
                target_path = fmap.parent.parent / str(target)
                if not target_path.exists():
                    errors.append(f"promoted reverse-PE b0 IntendedFor target is missing: {target_path}")
            checks.append(f"Promoted reverse-PE b0 fmap present={relative}")
        if sources and promoted and source_root.is_dir():
            for source_relative, fmap_relative in zip(sources, promoted):
                source = source_root / source_relative
                fmap = processing_root / fmap_relative
                source_json = sidecar(source, ".json")
                fmap_json = sidecar(fmap, ".json")
                if not source_json.exists() or not fmap_json.exists():
                    continue
                source_metadata = json.loads(source_json.read_text())
                fmap_metadata = json.loads(fmap_json.read_text())
                target_metadata = []
                for target in fmap_metadata.get("IntendedFor", []):
                    target_json = sidecar(fmap.parent.parent / str(target), ".json")
                    if target_json.exists():
                        target_metadata.append(json.loads(target_json.read_text()))
                if not target_metadata or any(
                    not opposite_pe(
                        source_metadata.get("PhaseEncodingDirection"),
                        target.get("PhaseEncodingDirection"),
                    )
                    for target in target_metadata
                ):
                    errors.append(f"promoted reverse-PE b0 is not opposite to directional DWI PE: {fmap}")
                source_readout = source_metadata.get("TotalReadoutTime")
                fmap_readout = fmap_metadata.get("TotalReadoutTime")
                if isinstance(source_readout, (int, float)) and isinstance(fmap_readout, (int, float)):
                    if not math.isclose(float(source_readout), float(fmap_readout), rel_tol=1e-6, abs_tol=1e-6):
                        errors.append(f"promoted reverse-PE b0 readout differs from source DWI: {fmap}")
                    for target in target_metadata:
                        target_readout = target.get("TotalReadoutTime")
                        if not isinstance(target_readout, (int, float)) or not math.isclose(
                            float(source_readout), float(target_readout), rel_tol=1e-6, abs_tol=1e-6
                        ):
                            errors.append(f"promoted reverse-PE b0 readout differs from a directional DWI: {fmap}")
                source_bval = sidecar(source, ".bval")
                if source_bval.exists() and not all(float(value) == 0 for value in source_bval.read_text().split()):
                    errors.append(f"promoted reverse-PE b0 source is not all-zero bval: {source}")
            checks.append("Promoted reverse-PE b0 PE polarity/readout cross-check passed")
        for relative in overlay.get("ExcludedEPIFieldmaps", []):
            if (processing_root / relative).exists():
                errors.append(f"excluded EPI fieldmap remains in processing copy: {processing_root / relative}")
        if sources and not promoted:
            errors.append("overlay records reverse-PE b0 sources but no promoted fieldmap")
        checks.append(f"Processing overlay validated sources={len(sources)} promoted={len(promoted)}")
    except Exception as exc:
        errors.append(f"cannot inspect processing overlay {path}: {exc}")


def check_topup_provenance(
    work_dir: Path,
    overlay_path: Path | None,
    errors: list[str],
    checks: list[str],
    required: bool,
) -> None:
    """Confirm that the completed QSIPrep graph used the prepared DWI b0."""
    try:
        selection_tables = sorted(work_dir.glob("**/hmc_sdc_wf/gather_inputs/b0_selection_info.tsv"))
        datain_files = sorted(work_dir.glob("**/hmc_sdc_wf/gather_inputs/topup_datain.txt"))
        topup_outputs = sorted(work_dir.glob("**/hmc_sdc_wf/topup/fieldmap_HZ.nii.gz"))
        corrected_outputs = sorted(work_dir.glob("**/hmc_sdc_wf/topup/topup_imain_corrected.nii.gz"))
        if not selection_tables or not datain_files:
            if required:
                errors.append(f"missing completed QSIPrep TOPUP provenance under {work_dir}")
            return
        if not topup_outputs or not corrected_outputs:
            if required:
                errors.append(f"missing completed TOPUP output images under {work_dir}")
            return

        selected_origins: set[str] = set()
        for table in selection_tables:
            rows = table.read_text().splitlines()
            if not rows:
                continue
            header = rows[0].split("\t")
            try:
                origin_index = header.index("bids_origin_file")
                selected_index = header.index("selected_for_sdc")
            except ValueError:
                errors.append(f"TOPUP selection table has an unexpected schema: {table}")
                continue
            for row in rows[1:]:
                fields = row.split("\t")
                if len(fields) > max(origin_index, selected_index) and fields[selected_index].lower() == "true":
                    selected_origins.add(fields[origin_index])

        if not selected_origins:
            errors.append(f"TOPUP selection table has no selected b0: {selection_tables[0]}")
        else:
            checks.append(f"QSIPrep TOPUP selected b0 volumes={len(selected_origins)}")

        if overlay_path and overlay_path.exists():
            overlay = json.loads(overlay_path.read_text())
            promoted = overlay.get("DWIReversePEB0Fieldmaps", [])
            expected = {"/bids/" + str(relative).lstrip("/") for relative in promoted}
            if expected and not expected.intersection(selected_origins):
                errors.append(
                    "QSIPrep TOPUP did not select the promoted reverse-PE b0: "
                    + ", ".join(sorted(expected))
                )
            elif expected:
                checks.append("QSIPrep TOPUP selected the promoted reverse-PE b0")

        datain_rows = [line.split() for line in datain_files[0].read_text().splitlines() if line.strip()]
        if len(datain_rows) < 2:
            errors.append(f"TOPUP datain has fewer than two PE rows: {datain_files[0]}")
        else:
            vectors = [topup_pe_vector(row) for row in datain_rows]
            if any(vector is None for vector in vectors):
                errors.append(f"TOPUP datain contains an invalid PE vector: {datain_files[0]}")
            else:
                unique_vectors = {vector for vector in vectors if vector is not None}
                if len(unique_vectors) < 2 or not any(
                    tuple(-value for value in vector) in unique_vectors for vector in unique_vectors
                ):
                    errors.append(f"TOPUP datain does not contain opposite PE vectors: {datain_files[0]}")
                try:
                    readouts = [float(row[3]) for row in datain_rows]
                    if any(not math.isfinite(value) or value <= 0 for value in readouts):
                        errors.append(f"TOPUP datain contains an invalid readout time: {datain_files[0]}")
                    elif any(
                        not math.isclose(readouts[0], value, rel_tol=1e-6, abs_tol=1e-6)
                        for value in readouts[1:]
                    ):
                        errors.append(f"TOPUP datain readout times are inconsistent: {datain_files[0]}")
                except (IndexError, ValueError):
                    errors.append(f"TOPUP datain contains an invalid readout time: {datain_files[0]}")
                if not any(error.endswith(str(datain_files[0])) for error in errors):
                    checks.append("QSIPrep TOPUP datain contains opposite PE vectors with consistent readout")
        checks.append("QSIPrep TOPUP fieldmap and corrected reference present")
    except Exception as exc:
        errors.append(f"cannot inspect QSIPrep TOPUP provenance under {work_dir}: {exc}")


def main() -> int:
    args = parse_args()
    root = args.output_dir.resolve()
    manifest = json.loads(args.manifest.read_text())
    errors: list[str] = []
    checks: list[str] = []
    if args.processing_overlay:
        if args.processing_overlay.exists():
            check_processing_overlay(args.processing_overlay, errors, checks)
        else:
            errors.append(f"processing overlay not found: {args.processing_overlay}")
    if args.require_topup_provenance and not args.qsiprep_work_dir:
        errors.append("--require-topup-provenance requires --qsiprep-work-dir")
    if args.qsiprep_work_dir:
        check_topup_provenance(
            args.qsiprep_work_dir.resolve(),
            args.processing_overlay,
            errors,
            checks,
            args.require_topup_provenance,
        )

    qsiprep = root / "qsiprep"
    qsirecon = root / "qsirecon"
    if not (qsiprep / "dataset_description.json").exists():
        errors.append("missing QSIPrep dataset_description.json")
    else:
        checks.append("QSIPrep dataset description present")
    if not (qsirecon / "dataset_description.json").exists():
        errors.append("missing QSIRecon dataset_description.json")
    else:
        checks.append("QSIRecon dataset description present")

    subject = f"sub-{args.participant_label.removeprefix('sub-')}" if args.participant_label else None
    if subject and subject not in manifest.get("Participants", []):
        errors.append(f"preflight manifest does not mention {subject}")
    preproc = sorted(qsiprep.glob(f"{subject or 'sub-*'}/**/*preproc_dwi.nii.gz"))
    masks: list[Path] = []
    reports = sorted(qsiprep.glob("**/*.html"))
    if not preproc:
        errors.append("no QSIPrep preprocessed DWI images found")
    else:
        for path in preproc:
            check_dwi(path, errors, checks)
            mask_name = path.name.replace('_desc-preproc_dwi.nii.gz', '_desc-brain_mask.nii.gz')
            matching_masks = sorted(qsiprep.glob(f"**/{mask_name}"))
            if len(matching_masks) != 1:
                errors.append(
                    f"{path}: expected one matching QSIPrep DWI mask named {mask_name}, "
                    f"found {len(matching_masks)}"
                )
                continue
            mask = matching_masks[0]
            masks.append(mask)
            try:
                dwi_image = nib.load(str(path))
                mask_image = nib.load(str(mask))
                if mask_image.shape != dwi_image.shape[:3]:
                    errors.append(f"{mask}: mask shape {mask_image.shape} != DWI shape {dwi_image.shape[:3]}")
                if not np.allclose(mask_image.affine, dwi_image.affine, atol=1e-4):
                    errors.append(f"{mask}: mask affine does not match its preprocessed DWI")
            except Exception as exc:
                errors.append(f"{mask}: cannot inspect DWI mask: {exc}")
        checks.append(f"QSIPrep preprocessed DWI={len(preproc)}")
    if masks:
        checks.append(f"QSIPrep DWI masks={len(masks)}")
    else:
        errors.append("no QSIPrep DWI masks found")
    checks.append(f"QSIPrep HTML reports={len(reports)}")

    # QSIRecon 26 writes a nested BIDS derivatives dataset below qsirecon/.
    recon_files = sorted(
        path for path in qsirecon.rglob("*")
        if path.is_file() and (subject is None or subject in path.parts or path.name.startswith(f"{subject}_"))
    )
    matrices = [
        path for path in recon_files
        if path.suffix.lower() in {".mat", ".csv", ".tsv"}
        and any(token in path.name.lower() for token in ("connect", "matrix", "connectome"))
    ]
    recon_qc = [path for path in recon_files if path.suffix.lower() in {".html", ".svg"}]
    tractography = [
        path for path in recon_files
        if path.suffix.lower() in {".tck", ".trk"}
        or path.name.lower().endswith(
            (".tck.gz", ".trk.gz", "_tractography.tck", "_tractography.trk")
        )
    ]
    if matrices:
        for path in matrices:
            check_connectivity_table(path, errors, checks)
        checks.append(f"QSIRecon connectivity tables={len(matrices)}")
    else:
        errors.append("no QSIRecon connectivity matrix/table outputs found")
    if tractography:
        for path in tractography:
            check_tractogram(path, errors, checks)
        checks.append(f"QSIRecon tractography files={len(tractography)}")
    else:
        errors.append("no QSIRecon tractography files found")
    checks.append(f"QSIRecon HTML/QC files={len(recon_qc)}")

    atlas_names = sorted(
        {
            atlas_name
            for path in matrices
            for atlas_name in connectivity_atlas_names(path)
        }
    )
    if atlas_names:
        checks.append(f"QSIRecon atlas-like matrix parents={','.join(atlas_names)}")
    else:
        errors.append("could not identify atlas-specific QSIRecon matrix outputs")
    if args.expected_atlas:
        output_names = "\n".join(str(path) for path in matrices).lower()
        observed_atlases = {name.lower() for name in atlas_names}
        missing_atlases = [
            atlas
            for atlas in args.expected_atlas
            if atlas.lower() not in observed_atlases and atlas.lower() not in output_names
        ]
        if missing_atlases:
            errors.append(f"missing expected QSIRecon atlas outputs: {', '.join(missing_atlases)}")
        else:
            checks.append(f"Expected QSIRecon atlases present={','.join(args.expected_atlas)}")

    report = {
        "SchemaVersion": "1.0.0",
        "OutputRoot": str(root),
        "PreflightManifest": str(args.manifest),
        "ProcessingOverlay": str(args.processing_overlay) if args.processing_overlay else None,
        "ExpectedAtlases": args.expected_atlas,
        "Checks": checks,
        "Errors": errors,
        "Status": "PASS" if not errors else "FAIL",
        "Notes": [
            "QSIRecon connectivity outputs are accepted in native matrix/table formats (.mat, .csv, or .tsv); atlas and matrix provenance should be read from QSIRecon sidecars/manifest.",
            f"Preflight warnings inherited: {len(manifest.get('Warnings', []))}",
        ],
    }
    report_path = args.output_json or root / "dwi_verification.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    for check in checks:
        print(f"CHECK: {check}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"DWI verification: {report['Status']}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
