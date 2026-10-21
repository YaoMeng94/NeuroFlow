#!/usr/bin/env python3
"""Package a volumetric label atlas as a minimal BIDS-Atlas dataset."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

import nibabel as nib
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-image", type=Path, required=True)
    parser.add_argument("--source-labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--atlas-name", required=True)
    parser.add_argument("--provenance", type=Path, help="optional source provenance JSON")
    parser.add_argument(
        "--template", default="MNI152NLin2009cAsym", help="BIDS template entity"
    )
    parser.add_argument(
        "--source-template",
        required=True,
        help="template space of the source image; must equal --template",
    )
    parser.add_argument(
        "--resolution-label", default="01", help="BIDS resolution entity label"
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def labels_from_text(path: Path) -> list[str]:
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if lines and lines[0].lower() == "brainnetome atlas":
        lines = lines[1:]
    if not lines:
        raise SystemExit(f"No atlas labels found in {path}")
    if all("," in line for line in lines):
        rows = list(csv.reader(lines))
        try:
            indices = [int(row[0]) for row in rows]
        except (ValueError, IndexError) as exc:
            raise SystemExit(f"Invalid indexed CSV label table: {path}") from exc
        if indices != list(range(1, len(rows) + 1)) or any(len(row) < 2 for row in rows):
            raise SystemExit(f"CSV labels must use consecutive indices starting at 1: {path}")
        return [row[1] for row in rows]
    return lines


def main() -> int:
    args = parse_args()
    if not args.source_image.is_file():
        raise SystemExit(f"Atlas image not found: {args.source_image}")
    if not args.source_labels.is_file():
        raise SystemExit(f"Atlas label file not found: {args.source_labels}")
    if args.provenance and not args.provenance.is_file():
        raise SystemExit(f"Atlas provenance file not found: {args.provenance}")
    if not args.atlas_name or any(char in args.atlas_name for char in " /\\"):
        raise SystemExit("--atlas-name must be a BIDS-safe non-empty value")
    if args.source_template != args.template:
        raise SystemExit(
            "Source and output templates differ; apply a validated label-preserving "
            "transform first, then package the transformed image"
        )

    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()) and not args.overwrite:
        raise SystemExit(f"Output is non-empty; use --overwrite: {output}")
    output.mkdir(parents=True, exist_ok=True)

    image = nib.load(str(args.source_image))
    if len(image.shape) != 3:
        raise SystemExit(f"Atlas image must be 3D, got shape {image.shape}")
    data = np.asanyarray(image.dataobj)
    if not np.isfinite(data).all():
        raise SystemExit("Atlas image contains non-finite values")
    labels = labels_from_text(args.source_labels)
    observed = data[data > 0]
    if observed.size and int(np.max(observed)) > len(labels):
        raise SystemExit(
            f"Atlas labels cover {len(labels)} regions but image contains label "
            f"{int(np.max(observed))}"
        )

    template_dir = output / f"tpl-{args.template}"
    template_dir.mkdir(parents=True, exist_ok=True)
    stem = f"tpl-{args.template}_atlas-{args.atlas_name}_res-{args.resolution_label}_dseg"
    image_target = template_dir / f"{stem}.nii.gz"
    json_target = template_dir / f"{stem}.json"
    tsv_target = template_dir / f"{stem}.tsv"
    shutil.copy2(args.source_image, image_target)
    if args.provenance:
        shutil.copy2(args.provenance, output / "atlas_provenance.json")

    tsv_target.write_text(
        "index\tname\n"
        + "".join(f"{index}\t{name}\n" for index, name in enumerate(labels, 1))
    )
    json_target.write_text(
        json.dumps(
            {
                "AtlasName": args.atlas_name,
                "AtlasDescription": "Volumetric label atlas packaged for QSIRecon.",
                "Template": args.template,
                "SourceTemplate": args.source_template,
                "ResolutionLabel": args.resolution_label,
                "SourceImage": str(args.source_image.resolve()),
                "SourceLabels": str(args.source_labels.resolve()),
                "SourceProvenance": "atlas_provenance.json" if args.provenance else None,
                "LabelCount": len(labels),
            },
            indent=2,
        )
        + "\n"
    )
    (output / "dataset_description.json").write_text(
        json.dumps(
            {
                "Name": f"{args.atlas_name} BIDS-Atlas dataset",
                "BIDSVersion": "1.10.0",
                "DatasetType": "derivative",
                "GeneratedBy": [
                    {
                        "Name": "Prisma DWI atlas packager",
                        "Version": "0.1.0",
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote atlas dataset: {output}")
    print(f"Image: {image_target}")
    print(f"Labels: {len(labels)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
