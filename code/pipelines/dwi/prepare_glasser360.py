#!/usr/bin/env python3
"""Split Neuroparc's bilateral Glasser labels into 360 hemisphere-specific IDs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import nibabel as nib
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-image", type=Path, required=True)
    parser.add_argument("--source-labels", type=Path, required=True)
    parser.add_argument("--output-image", type=Path, required=True)
    parser.add_argument("--output-labels", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()

    image = nib.load(str(args.source_image))
    data = np.asanyarray(image.dataobj)
    observed = set(np.unique(data).astype(int)) - {0}
    if observed != set(range(1, 181)):
        raise SystemExit("Expected Neuroparc Glasser labels 1 through 180")

    with args.source_labels.open(newline="") as handle:
        rows = [(int(index), name) for index, name in csv.reader(handle)]
    if [index for index, _ in rows] != list(range(1, 181)):
        raise SystemExit("Expected a 180-row, consecutively indexed label table")

    ijk = np.indices(image.shape, dtype=np.float32).reshape(3, -1).T
    xyz = nib.affines.apply_affine(image.affine, ijk).reshape((*image.shape, 3))
    right = xyz[..., 0] > 0
    output = data.astype(np.int16, copy=True)
    output[(output > 0) & right] += 180
    labels = [(index, f"L_{name}") for index, name in rows]
    labels += [(index + 180, f"R_{name}") for index, name in rows]
    if set(np.unique(output).astype(int)) - {0} != set(range(1, 361)):
        raise SystemExit("Hemisphere split did not produce labels 1 through 360")

    args.output_image.parent.mkdir(parents=True, exist_ok=True)
    nib.Nifti1Image(output, image.affine, image.header).to_filename(args.output_image)
    args.output_labels.write_text("".join(f"{index},{name}\n" for index, name in labels))
    args.provenance.write_text(
        json.dumps(
            {
                "SourceImage": str(args.source_image.resolve()),
                "SourceLabels": str(args.source_labels.resolve()),
                "SourceAtlas": "Neuroparc Glasser volumetric atlas",
                "SourceURL": "https://github.com/neurodata/neuroparc",
                "SourceLicense": "Apache-2.0",
                "SourceLicenseURL": "https://github.com/neurodata/neuroparc/blob/master/LICENSE.md",
                "AtlasReference": "Glasser et al. Nature 2016;536:171-178. doi:10.1038/nature18933",
                "SourceTemplate": "MNI152NLin6",
                "Method": "Labels 1-180 retained for world x<=0; right-hemisphere voxels at world x>0 offset by 180.",
                "LabelCount": 360,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote {args.output_image} with 360 labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
