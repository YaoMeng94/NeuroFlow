#!/usr/bin/env python3
"""Synthetic regression test for post-tedana GSR and non-GSR outputs."""

from __future__ import annotations

import csv
import subprocess
import tempfile
from pathlib import Path

import nibabel as nib
import numpy as np


def main() -> int:
    script = Path(__file__).with_name("regress_bold_confounds.py")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fmriprep = root / "fmriprep/sub-01/func"
        tedana = root / "tedana/sub-01/func"
        output = root / "post"
        fmriprep.mkdir(parents=True)
        tedana.mkdir(parents=True)
        stem = "sub-01_task-rest_run-1"
        frames = 40
        motion = np.sin(np.linspace(0, 4 * np.pi, frames))
        global_signal = np.cos(np.linspace(0, 3 * np.pi, frames))
        data = np.zeros((2, 2, 2, frames), dtype=np.float32)
        for voxel in range(8):
            data.reshape(8, frames)[voxel] = 100 + (voxel + 1) * global_signal + motion
        affine = np.eye(4)
        nib.Nifti1Image(data, affine).to_filename(tedana / f"{stem}_desc-denoised_bold.nii.gz")
        nib.Nifti1Image(np.ones((2, 2, 2), dtype=np.uint8), affine).to_filename(
            fmriprep / f"{stem}_desc-brain_mask.nii.gz"
        )
        columns = ("trans_x", "trans_y", "trans_z", "rot_x", "rot_y", "rot_z")
        with (fmriprep / f"{stem}_desc-confounds_timeseries.tsv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
            writer.writeheader()
            for index in range(frames):
                writer.writerow({name: motion[index] if name == "trans_x" else 0.0 for name in columns})
        subprocess.run([
            "python3", str(script), "--fmriprep-dir", str(root / "fmriprep"),
            "--tedana-dir", str(root / "tedana"), "--output-dir", str(output),
        ], check=True)
        gsr = nib.load(str(next(output.rglob("*_desc-tedanaGSR_bold.nii.gz")))).get_fdata()
        nogsr = nib.load(str(next(output.rglob("*_desc-tedanaNoGSR_bold.nii.gz")))).get_fdata()
        assert gsr.shape == data.shape == nogsr.shape
        assert np.isfinite(gsr).all() and np.isfinite(nogsr).all()
        assert np.std(gsr.mean(axis=(0, 1, 2))) < np.std(nogsr.mean(axis=(0, 1, 2))) * 1e-4
    print("post-tedana regression test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
