#!/usr/bin/env python3
"""Exclude masked voxels with no finite, nonzero diffusion signal."""

from __future__ import annotations

import argparse
from pathlib import Path

import nibabel as nib
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dwi", type=Path)
    parser.add_argument("brain_mask", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    dwi_image = nib.load(str(args.dwi))
    mask_image = nib.load(str(args.brain_mask))
    if len(dwi_image.shape) != 4:
        raise ValueError(f"Expected 4D DWI, got {dwi_image.shape}")
    if dwi_image.shape[:3] != mask_image.shape[:3]:
        raise ValueError("DWI and brain-mask shapes differ")
    if not np.allclose(dwi_image.affine, mask_image.affine, atol=1e-4, rtol=0):
        raise ValueError("DWI and brain-mask affines differ")

    mask = np.asanyarray(mask_image.dataobj) > 0
    signal = np.asanyarray(dwi_image.dataobj, dtype=np.float32)
    valid_signal = np.isfinite(signal).all(axis=3) & np.any(signal != 0, axis=3)
    effective_mask = mask & valid_signal
    removed = int(np.count_nonzero(mask & ~valid_signal))

    header = mask_image.header.copy()
    header.set_data_dtype(np.uint8)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(effective_mask.astype(np.uint8), mask_image.affine, header), args.output)
    print(
        f"Tensor mask voxels={int(effective_mask.sum())}; "
        f"excluded no-signal/non-finite voxels={removed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
