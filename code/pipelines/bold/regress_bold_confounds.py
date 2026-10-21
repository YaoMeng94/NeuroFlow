#!/usr/bin/env python3
"""Create full-length post-tedana GSR and non-GSR native-space BOLD series."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import nibabel as nib
import numpy as np


MOTION = ["trans_x", "trans_y", "trans_z", "rot_x", "rot_y", "rot_z"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fmriprep-dir", type=Path, required=True)
    parser.add_argument("--tedana-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def one(paths: list[Path], label: str) -> Path:
    if len(paths) != 1:
        raise RuntimeError(f"{label}: expected one file, found {len(paths)}")
    return paths[0]


def regress(data: np.ndarray, design: np.ndarray) -> np.ndarray:
    beta = np.linalg.lstsq(design, data, rcond=None)[0]
    residual = data - design @ beta
    return residual + np.mean(data, axis=0, keepdims=True)


def main() -> int:
    cfg = parse_args()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for source in sorted(cfg.tedana_dir.rglob("*_desc-denoised_bold.nii.gz")):
        stem = source.name.removesuffix("_desc-denoised_bold.nii.gz")
        subject = stem.split("_", 1)[0]
        confounds = one(
            sorted((cfg.fmriprep_dir / subject).rglob(f"{stem}_desc-confounds_timeseries.tsv")),
            f"{stem} confounds",
        )
        masks = [
            path for path in sorted((cfg.fmriprep_dir / subject).rglob(f"{stem}*desc-brain_mask.nii.gz"))
            if "_space-" not in path.name
        ]
        mask_path = one(masks, f"{stem} native brain mask")
        relative_parent = source.parent.relative_to(cfg.tedana_dir)
        out_dir = cfg.output_dir / relative_parent
        outputs = {
            "tedanaNoGSR": out_dir / f"{stem}_desc-tedanaNoGSR_bold.nii.gz",
            "tedanaGSR": out_dir / f"{stem}_desc-tedanaGSR_bold.nii.gz",
        }
        if cfg.dry_run:
            for output in outputs.values():
                print(output)
            count += 1
            continue

        image = nib.load(str(source))
        values = np.asanyarray(image.dataobj, dtype=np.float32)
        mask = np.asanyarray(nib.load(str(mask_path)).dataobj) > 0
        if values.ndim != 4 or values.shape[:3] != mask.shape:
            raise RuntimeError(f"{stem}: native mask and tedana BOLD grids differ")
        with confounds.open(newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            columns = reader.fieldnames or []
            rows = list(reader)
        if len(rows) != values.shape[3]:
            raise RuntimeError(f"{stem}: confounds rows do not match BOLD frames")
        missing = [name for name in MOTION if name not in columns]
        if missing:
            raise RuntimeError(f"{stem}: missing motion columns: {missing}")
        motion_rows = []
        for row in rows:
            motion_row = []
            for name in MOTION:
                try:
                    value = float(row[name])
                except (TypeError, ValueError):
                    value = 0.0
                motion_row.append(value if np.isfinite(value) else 0.0)
            motion_rows.append(motion_row)
        motion = np.asarray(motion_rows, dtype=float)
        derivatives = np.vstack((np.zeros((1, len(MOTION))), np.diff(motion, axis=0)))
        trend = np.linspace(-1.0, 1.0, values.shape[3])[:, None]
        intercept = np.ones((values.shape[3], 1))
        base = np.column_stack((intercept, trend, motion, derivatives))
        matrix = values[mask].T.astype(np.float64)
        global_signal = np.mean(matrix, axis=1, keepdims=True)
        global_derivative = np.vstack((np.zeros((1, 1)), np.diff(global_signal, axis=0)))

        variants = {
            "tedanaNoGSR": base,
            "tedanaGSR": np.column_stack((base, global_signal, global_derivative)),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        for label, design in variants.items():
            cleaned = np.zeros_like(values, dtype=np.float32)
            cleaned[mask] = regress(matrix, design).T.astype(np.float32)
            nib.Nifti1Image(cleaned, image.affine, image.header).to_filename(outputs[label])
            metadata = {
                "Sources": [str(source), str(confounds), str(mask_path)],
                "Description": "Full-length post-tedana nuisance-regressed BOLD; no frames removed.",
                "GlobalSignalRegression": label == "tedanaGSR",
                "NuisanceRegressors": [
                    "intercept", "linear_trend", *MOTION,
                    *[f"{name}_derivative1" for name in MOTION],
                    *(["global_signal", "global_signal_derivative1"] if label == "tedanaGSR" else []),
                ],
                "Regression": "All nuisance regressors were fitted simultaneously.",
                "MeanRestored": True,
                "Censoring": "None; use the separate FD-based temporal mask.",
            }
            outputs[label].with_suffix("").with_suffix(".json").write_text(
                json.dumps(metadata, indent=2) + "\n"
            )
        count += 1
    if not count:
        raise RuntimeError(f"No tedana denoised BOLD files found under {cfg.tedana_dir}")
    print(f"Created GSR and non-GSR post-tedana series for {count} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
