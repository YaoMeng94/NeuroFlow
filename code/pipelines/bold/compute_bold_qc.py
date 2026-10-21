#!/usr/bin/env python3
"""Compute multi-echo BOLD tSNR and FD-based clean-volume masks."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fmriprep-dir", type=Path, required=True)
    parser.add_argument("--tedana-dir", type=Path, required=True)
    parser.add_argument("--qc-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bids-dir", type=Path, help="original BIDS root used to recover scanner metadata")
    parser.add_argument("--input-space", default="native")
    parser.add_argument("--fd-threshold", type=float, default=0.20)
    parser.add_argument(
        "--comparison-echo",
        type=int,
        default=None,
        help="explicit single-echo baseline; otherwise choose the echo closest to --comparison-te-ms",
    )
    parser.add_argument(
        "--comparison-te-ms",
        type=float,
        default=None,
        help="override automatic target TE; starting defaults are 30 ms at 3 T and 20 ms at 7 T",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def write_dataset_description(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    description = {
        "Name": "Prisma BOLD quality-control derivatives",
        "BIDSVersion": "1.8.0",
        "DatasetType": "derivative",
        "GeneratedBy": [
            {
                "Name": "Prisma BOLD QC",
                "Version": "0.1.0",
                "Description": "tSNR summaries/maps and FD-based clean-volume masks",
            }
        ],
    }
    (output / "dataset_description.json").write_text(
        json.dumps(description, indent=2) + "\n"
    )


def find_echoes(root: Path, group_id: str, echo_count: int, space: str) -> list[Path]:
    pattern = re.compile(
        rf"^{re.escape(group_id)}_echo-(\d+)(?P<rest>.*)_desc-preproc_bold\.nii\.gz$"
    )
    by_echo: dict[int, Path] = {}
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
        by_echo[int(match.group(1))] = path
    missing = [echo for echo in range(1, echo_count + 1) if echo not in by_echo]
    if missing:
        raise FileNotFoundError(
            f"{group_id}: fMRIPrep corrected echoes missing for {space}: {missing}"
        )
    return [by_echo[echo] for echo in range(1, echo_count + 1)]


def sidecar_for_nifti(path: Path) -> Path:
    return path.with_suffix("").with_suffix(".json")


def infer_field_strength_t(
    path: Path,
    group: dict[str, Any],
    bids_dir: Path | None = None,
    group_id: str | None = None,
) -> float | None:
    for key in ("MagneticFieldStrength", "field_strength_t"):
        value = group.get(key)
        if value is not None:
            return float(value)
    sidecar = sidecar_for_nifti(path)
    if sidecar.exists():
        value = json.loads(sidecar.read_text()).get("MagneticFieldStrength")
        if value is not None:
            return float(value)
    if bids_dir is not None and group_id is not None:
        candidates = sorted(bids_dir.rglob(f"{group_id}_echo-1*_bold.json"))
        for candidate in candidates:
            value = json.loads(candidate.read_text()).get("MagneticFieldStrength")
            if value is not None:
                return float(value)
    return None


DEFAULT_COMPARISON_TE_3T_MS = 30.0
DEFAULT_COMPARISON_TE_7T_MS = 20.0


def default_target_te_ms(field_strength_t: float | None) -> float:
    """Return the field-strength-aware single-echo comparison target.

    Thirty milliseconds is the project default for 3T data. 7T data uses a
    separate 20 ms starting target because its shorter effective T2* changes
    the sensible single-echo comparison point. The explicit CLI override
    remains the authoritative option for unusual protocols or field strengths.
    """
    if field_strength_t is not None and abs(field_strength_t - 7.0) < 0.75:
        return DEFAULT_COMPARISON_TE_7T_MS
    return DEFAULT_COMPARISON_TE_3T_MS


def select_comparison_echo(
    echo_times: list[float | None], echo_count: int, target_te_ms: float,
    requested_echo: int | None = None,
) -> tuple[int, str]:
    if requested_echo is not None:
        if 1 <= requested_echo <= echo_count:
            return requested_echo, "explicit-echo"
        return 1, "explicit-echo-fallback-echo-1"
    valid_echo_times = [
        (index, float(echo_times[index - 1]))
        for index in range(1, echo_count + 1)
        if index <= len(echo_times) and echo_times[index - 1] is not None
    ]
    if not valid_echo_times:
        raise ValueError("no echo times available for automatic comparison selection")
    selected = min(
        valid_echo_times,
        key=lambda item: abs(item[1] - target_te_ms / 1000.0),
    )[0]
    return selected, "closest-TE"


def find_mask(root: Path, group_id: str, space: str) -> Path:
    candidates = sorted(root.rglob(f"{group_id}*desc-brain_mask.nii.gz"))
    for path in candidates:
        if space == "native" and "_space-" not in path.name:
            return path
        if space == "T1w" and "_space-T1w" in path.name:
            return path
        if space != "T1w" and f"_space-{space}" in path.name:
            return path
    raise FileNotFoundError(f"{group_id}: no {space} fMRIPrep brain mask found")


def find_confounds(root: Path, group_id: str) -> Path:
    candidates = sorted(root.rglob(f"{group_id}_desc-confounds_timeseries.tsv"))
    if not candidates:
        raise FileNotFoundError(f"{group_id}: fMRIPrep confounds TSV not found")
    return candidates[0]


def find_tedana(root: Path, group_id: str, descriptor: str) -> Path | None:
    candidates = sorted(root.rglob(f"{group_id}_desc-{descriptor}_bold.nii.gz"))
    return candidates[0] if candidates else None


def close_run_lengths(mask: np.ndarray) -> list[int]:
    lengths: list[int] = []
    current = 0
    for value in mask:
        if value:
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    if current:
        lengths.append(current)
    return lengths


def read_clean_mask(path: Path, fd_threshold: float) -> tuple[np.ndarray, dict[str, Any]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise ValueError(f"{path}: no confound rows")
    fieldnames = rows[0].keys()
    if "framewise_displacement" not in fieldnames:
        raise ValueError(f"{path}: framewise_displacement column is missing")

    mask: list[bool] = []
    fd_values: list[float | None] = []
    nonsteady_values: list[bool] = []
    nonsteady_columns = [
        name for name in fieldnames if name.startswith("non_steady_state_outlier")
    ]
    for row in rows:
        raw_fd = row.get("framewise_displacement", "")
        try:
            fd = float(raw_fd)
        except (TypeError, ValueError):
            fd = math.nan
        fd_values.append(None if not math.isfinite(fd) else fd)
        nonsteady = any(row.get(name, "0").strip().lower() in {"1", "1.0", "true"}
                         for name in nonsteady_columns)
        nonsteady_values.append(nonsteady)
        mask.append(math.isfinite(fd) and fd <= fd_threshold and not nonsteady)

    clean = np.asarray(mask, dtype=bool)
    run_lengths = close_run_lengths(clean)
    finite_fd = np.asarray([value for value in fd_values if value is not None], dtype=float)
    metadata: dict[str, Any] = {
        "FDThresholdMm": fd_threshold,
        "FramewiseDisplacementColumn": "framewise_displacement",
        "InitialNaNFDIsClean": False,
        "NonSteadyStateColumns": nonsteady_columns,
        "Volumes": len(rows),
        "CleanVolumes": int(clean.sum()),
        "ExcludedVolumes": int((~clean).sum()),
        "CleanFraction": float(clean.mean()),
        "LongestContiguousCleanRun": max(run_lengths, default=0),
        "CleanRunLengths": run_lengths,
        "FiniteFDVolumes": int(finite_fd.size),
        "FDMeanMm": float(finite_fd.mean()) if finite_fd.size else None,
        "FDMedianMm": float(np.median(finite_fd)) if finite_fd.size else None,
        "FDMaximumMm": float(finite_fd.max()) if finite_fd.size else None,
    }
    return clean, {"rows": rows, "metadata": metadata, "nonsteady": nonsteady_values, "fd": fd_values}


def write_clean_mask(
    output_dir: Path,
    group_id: str,
    clean: np.ndarray,
    confounds: dict[str, Any],
    fd_threshold: float,
) -> tuple[Path, Path]:
    threshold_label = f"{fd_threshold:.2f}".replace(".", "p")
    tsv = output_dir / f"{group_id}_desc-cleanFD{threshold_label}_timeseries.tsv"
    payload = confounds["rows"]
    with tsv.open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "volume_index",
            "framewise_displacement",
            "non_steady_state_outlier",
            "fd_clean",
            "clean",
        ])
        for index, row in enumerate(payload):
            fd_value = confounds["fd"][index]
            fd_clean = fd_value is not None and fd_value <= fd_threshold
            writer.writerow([
                index,
                row.get("framewise_displacement", "n/a") or "n/a",
                int(confounds["nonsteady"][index]),
                int(fd_clean),
                int(clean[index]),
            ])
    metadata = dict(confounds["metadata"])
    metadata.update({
        "Description": "One row per BOLD volume; clean requires finite FD <= threshold and no non-steady-state outlier.",
        "SourceConfounds": str(confounds.get("source", "")),
        "GeneratedAt": datetime.now(timezone.utc).isoformat(),
    })
    json_path = tsv.with_suffix("").with_suffix(".json")
    json_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return tsv, json_path


def image_values(path: Path, mask_path: Path) -> tuple[np.ndarray, nib.Nifti1Image, np.ndarray]:
    image = nib.load(str(path))
    mask_image = nib.load(str(mask_path))
    if image.shape[:3] != mask_image.shape[:3] or not np.allclose(
        image.affine, mask_image.affine, atol=1e-4
    ):
        raise ValueError(f"{path}: image grid does not match mask {mask_path}")
    if len(image.shape) != 4:
        raise ValueError(f"{path}: expected 4D BOLD image, got {image.shape}")
    data = np.asanyarray(image.dataobj).astype(np.float32, copy=False)
    mask = np.asanyarray(mask_image.dataobj) > 0
    if not np.isfinite(data).all():
        raise ValueError(f"{path}: contains non-finite values")
    return data, image, mask


def summary(values: np.ndarray, mask: np.ndarray, clean: np.ndarray | None) -> tuple[dict[str, Any], np.ndarray]:
    selected = values[mask]
    mean = selected.mean(axis=1)
    std = selected.std(axis=1, ddof=1)
    tsnr = np.divide(mean, std, out=np.zeros_like(mean), where=std > 0)
    tsnr_map = np.zeros(values.shape[:3], dtype=np.float32)
    tsnr_map[mask] = tsnr.astype(np.float32, copy=False)
    result: dict[str, Any] = {
        "Volumes": int(values.shape[3]),
        "MaskVoxels": int(mask.sum()),
        "MeanSignal": float(mean.mean()),
        "MedianSignal": float(np.median(mean)),
        "MeanTemporalStd": float(std.mean()),
        "MeanTSNR": float(tsnr.mean()),
        "MedianTSNR": float(np.median(tsnr)),
        "TSNRPercentiles": {
            "P05": float(np.percentile(tsnr, 5)),
            "P25": float(np.percentile(tsnr, 25)),
            "P75": float(np.percentile(tsnr, 75)),
            "P95": float(np.percentile(tsnr, 95)),
        },
    }
    if clean is not None and int(clean.sum()) >= 2:
        clean_values = values[..., clean]
        clean_selected = clean_values[mask]
        clean_mean = clean_selected.mean(axis=1)
        clean_std = clean_selected.std(axis=1, ddof=1)
        clean_tsnr = np.divide(
            clean_mean,
            clean_std,
            out=np.zeros_like(clean_mean),
            where=clean_std > 0,
        )
        result["CleanVolumes"] = int(clean.sum())
        result["CleanMeanTSNR"] = float(clean_tsnr.mean())
        result["CleanMedianTSNR"] = float(np.median(clean_tsnr))
        result["CleanTSNRPercentiles"] = {
            "P05": float(np.percentile(clean_tsnr, 5)),
            "P25": float(np.percentile(clean_tsnr, 25)),
            "P75": float(np.percentile(clean_tsnr, 75)),
            "P95": float(np.percentile(clean_tsnr, 95)),
        }
    else:
        result["CleanVolumes"] = int(clean.sum()) if clean is not None else 0
        result["CleanTSNRUnavailable"] = True
    return result, tsnr_map


def write_map(
    path: Path,
    image: nib.Nifti1Image,
    data: np.ndarray,
    metadata: dict[str, Any],
) -> None:
    header = image.header.copy()
    header.set_data_dtype(np.float32)
    output = nib.Nifti1Image(data, image.affine, header=header)
    output.set_qform(image.affine, code=1)
    output.set_sform(image.affine, code=1)
    nib.save(output, str(path))
    path.with_suffix("").with_suffix(".json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )


def main() -> int:
    config = parse_args()
    if config.fd_threshold < 0:
        raise SystemExit("--fd-threshold must be non-negative")
    if config.comparison_echo is not None and config.comparison_echo < 1:
        raise SystemExit("--comparison-echo must be a positive echo number")
    if config.comparison_te_ms is not None and config.comparison_te_ms <= 0:
        raise SystemExit("--comparison-te-ms must be positive")
    config.qc_dir.mkdir(parents=True, exist_ok=True)
    write_dataset_description(config.qc_dir)
    if config.dry_run:
        if config.manifest.exists():
            manifest = json.loads(config.manifest.read_text())
            groups = manifest.get("Groups", [])
            for group in groups:
                print(f"PLAN: BOLD QC for {group['group_id']} at FD <= {config.fd_threshold:.2f} mm")
        else:
            target = (
                f"explicit target TE {config.comparison_te_ms:g} ms"
                if config.comparison_te_ms is not None
                else "field-strength-aware target TE"
            )
            print(
                f"PLAN: BOLD QC for manifest groups at FD <= {config.fd_threshold:.2f} mm; {target}"
            )
        return 0
    manifest = json.loads(config.manifest.read_text())

    tsnr_dir = config.qc_dir / "tsnr"
    clean_dir = config.qc_dir / "clean_masks"
    tsnr_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for group in manifest.get("Groups", []):
        group_id = group["group_id"]
        echoes = find_echoes(config.fmriprep_dir, group_id, len(group["echoes"]), config.input_space)
        field_strength_t = infer_field_strength_t(
            echoes[0], group, config.bids_dir, group_id
        )
        target_te_ms = (
            config.comparison_te_ms
            if config.comparison_te_ms is not None
            else default_target_te_ms(field_strength_t)
        )
        mask_path = find_mask(config.fmriprep_dir, group_id, config.input_space)
        confounds_path = find_confounds(config.fmriprep_dir, group_id)
        clean, confounds = read_clean_mask(confounds_path, config.fd_threshold)
        confounds["source"] = str(confounds_path)
        clean_tsv, clean_json = write_clean_mask(
            clean_dir, group_id, clean, confounds, config.fd_threshold
        )

        inputs: list[tuple[str, Path]] = [(f"echo-{index}", path) for index, path in enumerate(echoes, 1)]
        echo_times = list(group.get("echo_times_seconds", []))
        echo_times = (echo_times + [None] * len(echoes))[:len(echoes)]
        used_echo, selection_method = select_comparison_echo(
            echo_times, len(echoes), target_te_ms, config.comparison_echo
        )
        comparison_label = f"echo-{used_echo}"
        for descriptor, label in (("optcom", "optcom"), ("denoised", "denoised")):
            path = find_tedana(config.tedana_dir, group_id, descriptor)
            if path is not None:
                inputs.append((label, path))
        group_record: dict[str, Any] = {
            "GroupID": group_id,
            "InputSpace": config.input_space,
            "MagneticFieldStrengthT": field_strength_t,
            "ComparisonTargetEchoTimeMs": target_te_ms,
            "BrainMask": str(mask_path),
            "Confounds": str(confounds_path),
            "CleanMaskTSV": str(clean_tsv),
            "CleanMaskJSON": str(clean_json),
            "FD": confounds["metadata"],
            "Inputs": {},
            "Warnings": [],
        }
        if field_strength_t is None and config.comparison_te_ms is None:
            group_record["Warnings"].append(
                "Magnetic field strength was not found; automatic comparison target defaults to the 3T value of 30 ms"
            )
        reference_shape: tuple[int, ...] | None = None
        for label, path in inputs:
            values, image, mask = image_values(path, mask_path)
            if reference_shape is None:
                reference_shape = values.shape
            if values.shape[3] != clean.size:
                raise ValueError(
                    f"{path}: {values.shape[3]} volumes but confounds contain {clean.size} rows"
                )
            metrics, tsnr_map = summary(values, mask, clean)
            metrics["Source"] = str(path)
            group_record["Inputs"][label] = metrics
            if label == comparison_label:
                map_name = f"{group_id}_echo-{used_echo}_desc-tsnr_bold.nii.gz"
            elif label == "optcom":
                map_name = f"{group_id}_desc-tsnrOptcom_bold.nii.gz"
            elif label == "denoised":
                map_name = f"{group_id}_desc-tsnrDenoised_bold.nii.gz"
            else:
                map_name = ""
            if map_name:
                map_metadata = {
                    "Description": "Voxelwise temporal signal-to-noise ratio (temporal mean divided by sample standard deviation).",
                    "Metric": "tSNR",
                    "SourceImage": str(path),
                    "BrainMask": str(mask_path),
                    "CleanMask": str(clean_tsv),
                    "CleanFDThresholdMm": config.fd_threshold,
                }
                if label == comparison_label:
                    map_metadata["ComparisonBaseline"] = {
                        "UsedEcho": used_echo,
                        "SelectionMethod": selection_method,
                        "TargetEchoTimeSeconds": target_te_ms / 1000.0,
                        "EchoTimeSeconds": echo_times[used_echo - 1],
                    }
                write_map(tsnr_dir / map_name, image, tsnr_map, map_metadata)

        optcom = group_record["Inputs"].get("optcom")
        echo1 = group_record["Inputs"].get("echo-1")
        if optcom and echo1:
            group_record["OptcomGainVsEcho1"] = {
                "MedianTSNRAbsolute": optcom["MedianTSNR"] - echo1["MedianTSNR"],
                "MedianTSNRRelative": optcom["MedianTSNR"] / echo1["MedianTSNR"] - 1.0
                if echo1["MedianTSNR"] else None,
                "CleanMedianTSNRAbsolute": optcom.get("CleanMedianTSNR", 0.0)
                - echo1.get("CleanMedianTSNR", 0.0),
                "CleanMedianTSNRRelative": (
                    optcom["CleanMedianTSNR"] / echo1["CleanMedianTSNR"] - 1.0
                    if optcom.get("CleanMedianTSNR") and echo1.get("CleanMedianTSNR")
                    else None
                ),
            }
            baseline = group_record["Inputs"][f"echo-{used_echo}"]
            group_record["ComparisonBaseline"] = {
                "RequestedEcho": config.comparison_echo,
                "UsedEcho": used_echo,
                "SelectionMethod": selection_method,
                "TargetEchoTimeSeconds": target_te_ms / 1000.0,
                "EchoTimeSeconds": echo_times[used_echo - 1],
                "Description": "Acquisition-level single-echo baseline for descriptive ME-fMRI gain.",
            }
            group_record["OptcomGainVsBaseline"] = {
                "MedianTSNRAbsolute": optcom["MedianTSNR"] - baseline["MedianTSNR"],
                "MedianTSNRRelative": optcom["MedianTSNR"] / baseline["MedianTSNR"] - 1.0
                if baseline["MedianTSNR"] else None,
                "CleanMedianTSNRAbsolute": optcom.get("CleanMedianTSNR", 0.0)
                - baseline.get("CleanMedianTSNR", 0.0),
                "CleanMedianTSNRRelative": (
                    optcom["CleanMedianTSNR"] / baseline["CleanMedianTSNR"] - 1.0
                    if optcom.get("CleanMedianTSNR") and baseline.get("CleanMedianTSNR")
                    else None
                ),
            }
        if not optcom:
            group_record["Warnings"].append("tedana optcom output not found")
        records.append(group_record)

    output = {
        "SchemaVersion": "1.0.0",
        "GeneratedAt": datetime.now(timezone.utc).isoformat(),
        "FDThresholdMm": config.fd_threshold,
        "ComparisonEchoRequested": config.comparison_echo,
        "ComparisonTargetTEPolicy": {
            "ExplicitOverrideMs": config.comparison_te_ms,
            "DefaultMsByFieldStrength": {
                "3T": DEFAULT_COMPARISON_TE_3T_MS,
                "7T": DEFAULT_COMPARISON_TE_7T_MS,
            },
        },
        "Records": records,
        "Notes": [
            "tSNR is temporal mean divided by sample standard deviation within the fMRIPrep brain mask.",
            "The default clean mask excludes non-finite initial FD rows and non-steady-state outliers.",
            "OptcomGainVsBaseline is descriptive QC, not a statistical performance claim; by default the baseline is the echo closest to 30 ms at 3T or 20 ms at 7T. The 3T value is the project standard; the 7T value is a configurable starting heuristic.",
        ],
    }
    result_path = config.qc_dir / "bold_qc_metrics.json"
    if result_path.exists():
        previous = json.loads(result_path.read_text()).get("Records", [])
        current_ids = {record["GroupID"] for record in records}
        output["Records"] = [record for record in previous if record["GroupID"] not in current_ids] + records
    result_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Wrote BOLD QC metrics: {result_path}")
    for record in records:
        baseline_echo = record.get("ComparisonBaseline", {}).get("UsedEcho")
        baseline_metrics = record["Inputs"].get(f"echo-{baseline_echo}", {}) if baseline_echo else {}
        print(
            f"QC: {record['GroupID']} clean={record['FD']['CleanVolumes']}/{record['FD']['Volumes']} "
            f"echo1_median_tSNR={record['Inputs']['echo-1']['MedianTSNR']:.3f} "
            f"echo{baseline_echo}_median_tSNR={baseline_metrics.get('MedianTSNR', float('nan')):.3f} "
            f"optcom_median_tSNR={record['Inputs'].get('optcom', {}).get('MedianTSNR', float('nan')):.3f}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
