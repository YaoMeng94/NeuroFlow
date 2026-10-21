#!/usr/bin/env python3
"""Measure spherical registration topology, alignment, and distortion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import nibabel as nib
import numpy as np


def data(path: Path) -> np.ndarray:
    return np.asarray(nib.load(str(path)).darrays[0].data, dtype=float)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sphere", type=Path, required=True)
    parser.add_argument("--registered-sulc", type=Path, required=True)
    parser.add_argument("--reference-sulc", type=Path, required=True)
    parser.add_argument("--areal-distortion", type=Path)
    parser.add_argument("--edge-distortion", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def summarize(values: np.ndarray) -> dict[str, float | int]:
    finite = values[np.isfinite(values)]
    return {
        "finite_values": int(finite.size),
        "minimum": float(finite.min()),
        "median": float(np.median(finite)),
        "p95_absolute": float(np.percentile(np.abs(finite), 95)),
        "p99_absolute": float(np.percentile(np.abs(finite), 99)),
        "maximum": float(finite.max()),
    }


def main() -> int:
    args = parse_args()
    sphere = nib.load(str(args.sphere))
    vertices = np.asarray(sphere.darrays[0].data, dtype=float)
    faces = np.asarray(sphere.darrays[1].data, dtype=int)
    a, b, c = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    orientation = np.einsum("ij,ij->i", np.cross(b - a, c - a), (a + b + c) / 3)
    nonzero = orientation[np.abs(orientation) > 1e-10]
    expected_sign = 1.0 if np.median(nonzero) > 0 else -1.0
    oriented = expected_sign * orientation

    moving = data(args.registered_sulc)
    reference = data(args.reference_sulc)
    valid = np.isfinite(moving) & np.isfinite(reference)
    report: dict[str, object] = {
        "sphere": str(args.sphere),
        "vertices": int(vertices.shape[0]),
        "triangles": int(faces.shape[0]),
        "flipped_triangles": int(np.sum(oriented < -1e-10)),
        "degenerate_triangles": int(np.sum(np.abs(oriented) <= 1e-10)),
        "minimum_oriented_area_proxy": float(oriented.min()),
        "sulcal_correlation": float(np.corrcoef(moving[valid], reference[valid])[0, 1]),
    }
    for label, path in (
        ("areal_distortion", args.areal_distortion),
        ("edge_distortion", args.edge_distortion),
    ):
        if path:
            report[label] = summarize(data(path))

    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return int(report["flipped_triangles"] != 0 or report["degenerate_triangles"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
