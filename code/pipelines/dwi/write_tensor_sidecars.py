#!/usr/bin/env python3
"""Write JSON sidecars for MRtrix tensor scalar maps."""

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    metadata = {
        "fa": ("Fractional anisotropy", "unitless"),
        "md": ("Mean diffusivity", "mm2/s"),
        "ad": ("Axial diffusivity", "mm2/s"),
        "rd": ("Radial diffusivity", "mm2/s"),
    }
    for parameter, (description, units) in metadata.items():
        path = args.output_dir / f"{args.prefix}_model-dti_param-{parameter}_dwimap.json"
        path.write_text(
            json.dumps(
                {
                    "Description": description,
                    "Units": units,
                    "Model": "Diffusion tensor",
                    "Software": "MRtrix3 dwi2tensor/tensor2metric",
                    "PositiveSemidefiniteProjection": "Negative fitted eigenvalues were set to zero before deriving scalar maps.",
                    "Sources": [f"bids:qsiprep:{args.source}"],
                },
                indent=2,
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
