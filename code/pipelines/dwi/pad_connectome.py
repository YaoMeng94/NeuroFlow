#!/usr/bin/env python3
"""Pad an MRtrix connectome to the complete atlas label order."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import nibabel as nib
import numpy as np


def atlas_labels(path: Path) -> list[int]:
    with path.open(newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t")
        return [int(row["index"]) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--segmentation", type=Path, required=True)
    parser.add_argument("--labels-tsv", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    args = parser.parse_args()

    matrix = np.loadtxt(args.matrix, delimiter=",")
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError(f"Connectome must be square, got {matrix.shape}")
    expected = atlas_labels(args.labels_tsv)
    seg_data = np.asanyarray(nib.load(str(args.segmentation)).dataobj)
    if not np.isfinite(seg_data).all() or not np.equal(seg_data, np.rint(seg_data)).all():
        raise ValueError("Segmentation contains non-finite or non-integer label values")
    observed = set(np.unique(seg_data).astype(int))
    observed.discard(0)
    if len(expected) != len(set(expected)) or any(label <= 0 for label in expected):
        raise ValueError("Atlas TSV indices must be unique positive integers")
    unexpected = observed - set(expected)
    if unexpected:
        raise ValueError(f"Segmentation contains labels absent from atlas TSV: {sorted(unexpected)}")
    missing = sorted(set(expected) - observed)
    if matrix.shape[0] > max(expected):
        raise ValueError(
            f"Matrix shape {matrix.shape} exceeds largest atlas index {max(expected)}"
        )
    if observed and max(observed) > matrix.shape[0]:
        raise ValueError(
            f"Matrix shape {matrix.shape} cannot represent observed label {max(observed)}"
        )

    padded = np.zeros((len(expected), len(expected)), dtype=float)
    positions = {label: index for index, label in enumerate(expected)}
    # MRtrix indexes matrix rows by numeric node label: row 0 is label 1,
    # even when some intermediate labels are absent from this subject's map.
    # Preserve those empty rows instead of compacting by observed-label order.
    for row in range(matrix.shape[0]):
        row_label = row + 1
        if row_label not in positions:
            continue
        for column in range(matrix.shape[1]):
            column_label = column + 1
            if column_label in positions:
                padded[positions[row_label], positions[column_label]] = matrix[row, column]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(args.output, padded, delimiter=",", fmt="%.9g")
    args.sidecar.write_text(
        json.dumps(
            {
                "AtlasLabels": str(args.labels_tsv),
                "NumberOfNodes": len(expected),
                "ObservedNodes": len(observed),
                "MissingLabels": missing,
                "PaddingPolicy": "Matrix rows map to numeric MRtrix node labels; absent subject-space parcels remain zero rows and columns.",
                "Symmetric": bool(np.allclose(padded, padded.T, rtol=1e-5, atol=1e-8)),
                "DiagonalZero": bool(np.allclose(np.diag(padded), 0.0)),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Connectome padded to {len(expected)} nodes; missing labels: {missing or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
