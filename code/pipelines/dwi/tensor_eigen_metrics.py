#!/usr/bin/env python3
"""Derive physical DTI scalars after projecting tensor eigenvalues to >= 0."""

from __future__ import annotations

import argparse
from pathlib import Path

import nibabel as nib
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eigenvalues", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("prefix")
    args = parser.parse_args()

    image = nib.load(str(args.eigenvalues))
    eigenvalues = np.asarray(image.dataobj, dtype=np.float32)
    if eigenvalues.ndim != 4 or eigenvalues.shape[-1] != 3:
        raise ValueError(f"Expected three eigenvalue volumes, got {eigenvalues.shape}")

    # MRtrix orders eigenvalues from largest to smallest. Projecting negative
    # values to zero produces a positive-semidefinite tensor before scalars.
    eigenvalues = np.maximum(eigenvalues, 0.0)
    ad = eigenvalues[..., 0]
    rd = eigenvalues[..., 1:].mean(axis=-1)
    md = eigenvalues.mean(axis=-1)
    denominator = np.square(eigenvalues).sum(axis=-1)
    numerator = np.square(eigenvalues - md[..., None]).sum(axis=-1)
    fa = np.zeros_like(md)
    valid = denominator > np.finfo(np.float32).eps
    fa[valid] = np.sqrt(1.5 * numerator[valid] / denominator[valid])
    fa = np.clip(fa, 0.0, 1.0)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    header = image.header.copy()
    header.set_data_shape(image.shape[:3])
    for parameter, data in {"fa": fa, "md": md, "ad": ad, "rd": rd}.items():
        output = args.output_dir / f"{args.prefix}_model-dti_param-{parameter}_dwimap.nii.gz"
        nib.save(nib.Nifti1Image(data.astype(np.float32), image.affine, header), output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
