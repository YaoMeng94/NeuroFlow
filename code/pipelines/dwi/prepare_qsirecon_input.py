#!/usr/bin/env python3
"""Stage a QSIRecon view with QSIPrep DWI masks in the DWI datatype folder."""

import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil

import nibabel as nib
import numpy as np


def link_or_copy(source: str, destination: str) -> str:
    try:
        os.link(source, destination)
        return destination
    except OSError:
        return shutil.copy2(source, destination)


def entities(path: Path) -> dict[str, str]:
    return {key: value for token in path.name.removesuffix(".nii.gz").split("_")[:-1]
            if "-" in token for key, value in [token.split("-", 1)]}


def shell_signature(bvals: np.ndarray) -> tuple[int, ...]:
    shells = []
    for value in sorted(float(v) for v in bvals if v > 50):
        if not shells or value - shells[-1][0] > 100:
            shells.append([value])
        else:
            shells[-1].append(value)
    return tuple(round(float(np.mean(shell))) for shell in shells)


def read_gradients(dwi: Path) -> tuple[np.ndarray, np.ndarray]:
    stem = dwi.name.removesuffix(".nii.gz")
    bvals = np.loadtxt(dwi.with_name(stem + ".bval")).reshape(-1)
    bvecs = np.loadtxt(dwi.with_name(stem + ".bvec"))
    if bvecs.shape != (3, len(bvals)):
        raise ValueError(f"Gradient dimensions do not match for {dwi}")
    image = nib.load(str(dwi))
    if len(image.shape) != 4 or image.shape[3] != len(bvals):
        raise ValueError(f"Volume/bval mismatch for {dwi}")
    if not np.isfinite(bvals).all() or not np.isfinite(bvecs).all() or (bvals < 0).any():
        raise ValueError(f"Invalid gradients in {dwi}")
    return bvals, bvecs


def source_sidecar(path: Path, root: Path, bids_root: Path | None) -> Path:
    derivative_sidecar = path.with_name(path.name.removesuffix(".nii.gz") + ".json")
    if derivative_sidecar.is_file():
        return derivative_sidecar
    if bids_root is not None:
        relative_parent = path.parent.relative_to(root)
        tokens = path.name.removesuffix(".nii.gz").split("_")[:-1]
        raw_entities = [token for token in tokens
                        if not token.startswith(("space-", "desc-", "model-", "stat-"))]
        raw_sidecar = bids_root / relative_parent / ("_".join(raw_entities) + "_dwi.json")
        if raw_sidecar.is_file():
            return raw_sidecar
    raise FileNotFoundError(
        f"Cannot resolve acquisition metadata for {path}; expected {derivative_sidecar}"
        + (f" or a matching raw BIDS sidecar under {bids_root}" if bids_root else "")
    )


def combine_acquisition_group(files: list[Path], root: Path, bids_root: Path | None) -> dict:
    """Concatenate compatible, motion/distortion-corrected acquisition outputs."""
    files = sorted(files)
    images = [nib.load(str(path)) for path in files]
    first = images[0]
    bvals_bvecs = [read_gradients(path) for path in files]
    sidecars = [json.loads(source_sidecar(path, root, bids_root).read_text()) for path in files]
    if not sidecars[0].get("PhaseEncodingDirection"):
        raise ValueError(f"Cannot combine acquisitions without PhaseEncodingDirection: {files[0]}")
    if sidecars[0].get("TotalReadoutTime") is None:
        raise ValueError(f"Cannot combine acquisitions without TotalReadoutTime: {files[0]}")
    for path, image, metadata in zip(files[1:], images[1:], sidecars[1:]):
        if image.shape[:3] != first.shape[:3] or not np.allclose(image.affine, first.affine, atol=1e-4):
            raise ValueError(f"Cannot combine acquisitions on different grids: {files[0]} and {path}")
        if metadata.get("PhaseEncodingDirection") != sidecars[0].get("PhaseEncodingDirection"):
            raise ValueError(f"Cannot combine acquisitions with different phase encoding: {files[0]} and {path}")
        if metadata.get("TotalReadoutTime") is None or sidecars[0].get("TotalReadoutTime") is None or not np.isclose(
                float(metadata["TotalReadoutTime"]), float(sidecars[0]["TotalReadoutTime"]), atol=1e-6):
            raise ValueError(f"Cannot combine acquisitions with missing or different readout time: {path}")
    signatures = [shell_signature(pair[0]) for pair in bvals_bvecs]
    if not signatures[0] or any(signature != signatures[0] for signature in signatures[1:]):
        raise ValueError(f"Cannot combine acquisitions with different diffusion shells: {files}")

    acq_values = [entities(path).get("acq") for path in files]
    if not all(acq_values) or len(set(acq_values)) != len(acq_values):
        raise ValueError(f"Acquisition group must have distinct acq entities: {files}")
    output_name = re.sub(r"_acq-[^_]+", "", files[0].name)
    output = files[0].with_name(output_name)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite combined output: {output}")

    arrays = [np.asarray(image.dataobj, dtype=np.float32) for image in images]
    data = np.concatenate(arrays, axis=3)
    nib.save(nib.Nifti1Image(data, first.affine, first.header), str(output))
    stem = output.name.removesuffix(".nii.gz")
    bvals = np.concatenate([pair[0] for pair in bvals_bvecs])
    bvecs = np.concatenate([pair[1] for pair in bvals_bvecs], axis=1)
    np.savetxt(output.with_name(stem + ".bval"), bvals[None, :], fmt="%.8g")
    np.savetxt(output.with_name(stem + ".bvec"), bvecs, fmt="%.10g")

    combined_metadata = dict(sidecars[0])
    combined_metadata["Sources"] = [source for metadata in sidecars
                                     for source in metadata.get("Sources", [])]
    combined_metadata["AcquisitionCombination"] = {
        "Method": "concatenation of QSIPrep-preprocessed acquisitions",
        "InputFiles": [str(path.relative_to(root)) for path in files],
        "InputVolumes": [int(image.shape[3]) for image in images],
        "InputAcquisitions": acq_values,
    }
    output.with_name(stem + ".json").write_text(json.dumps(combined_metadata, indent=2) + "\n")

    prefixes = [path.name.removesuffix("_desc-preproc_dwi.nii.gz") for path in files]
    confound_prefixes = [prefix.split("_space-")[0] for prefix in prefixes]
    confound_files = [path.with_name(prefix + "_desc-confounds_timeseries.tsv")
                      for path, prefix in zip(files, confound_prefixes)]
    if any(path.is_file() for path in confound_files) and not all(path.is_file() for path in confound_files):
        raise ValueError(f"Confounds are missing for part of the acquisition group: {confound_files}")
    if all(path.is_file() for path in confound_files):
        tables = []
        for path in confound_files:
            with path.open(newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                tables.append((reader.fieldnames, list(reader)))
        if any(fields != tables[0][0] for fields, _ in tables[1:]):
            raise ValueError(f"Confound columns differ across acquisitions: {confound_files}")
        if any(len(rows) != image.shape[3] for (_, rows), image in zip(tables, images)):
            raise ValueError(f"Confound row counts do not match DWI volumes: {confound_files}")
        confound_path = output.with_name(stem.split("_space-")[0] + "_desc-confounds_timeseries.tsv")
        with confound_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=tables[0][0], delimiter="\t")
            writer.writeheader()
            for _, rows in tables:
                writer.writerows(rows)

    mask_paths = []
    for path in files:
        mask_name = path.name.replace("_desc-preproc_dwi.nii.gz", "_desc-brain_mask.nii.gz")
        matches = sorted(root.glob(f"**/{mask_name}"))
        if len(matches) != 1:
            raise ValueError(f"Expected one source mask for {path}, found {len(matches)}")
        mask_paths.append(matches[0])
    if all(path.is_file() for path in mask_paths):
        masks = [nib.load(str(path)) for path in mask_paths]
        if any(mask.shape != first.shape[:3] or not np.allclose(mask.affine, first.affine, atol=1e-4)
               for mask in masks):
            raise ValueError(f"DWI mask grid mismatch in acquisition group: {files}")
        mask_data = np.logical_or.reduce([np.asarray(mask.dataobj) > 0 for mask in masks]).astype(np.uint8)
        mask_path = output.with_name(stem.replace("_desc-preproc_dwi", "_desc-brain_mask") + ".nii.gz")
        nib.save(nib.Nifti1Image(mask_data, first.affine), str(mask_path))
        for mask_path_source in mask_paths:
            if mask_path_source != mask_path:
                mask_path_source.unlink()

    b0_volumes = [array[..., bvals_for_file <= 50]
                  for array, (bvals_for_file, _) in zip(arrays, bvals_bvecs)]
    combined_b0 = np.concatenate(b0_volumes, axis=3)
    if combined_b0.shape[3] == 0:
        raise ValueError(f"Cannot create a QSIRecon b0 reference without b0 volumes: {files}")
    reference = combined_b0.mean(axis=3)
    reference_path = output.with_name(stem.replace("_desc-preproc_dwi", "_dwiref") + ".nii.gz")
    nib.save(nib.Nifti1Image(reference, first.affine), str(reference_path))

    # Remove only staged copies; source QSIPrep files remain untouched.
    for path, prefix, confound_prefix in zip(files, prefixes, confound_prefixes):
        old_stem = path.name.removesuffix(".nii.gz")
        for suffix in (".nii.gz", ".bval", ".bvec", ".json"):
            path.with_name(old_stem + suffix).unlink(missing_ok=True)
        for suffix in ("_dwiref.nii.gz",):
            path.with_name(prefix + suffix).unlink(missing_ok=True)
        path.with_name(confound_prefix + "_desc-confounds_timeseries.tsv").unlink(missing_ok=True)
    return {"Output": str(output.relative_to(root)), "Inputs": [str(p.relative_to(root)) for p in files],
            "Volumes": int(data.shape[3]), "Shells": list(signatures[0])}


def combine_compatible_acquisitions(destination: Path, bids_root: Path | None) -> list[dict]:
    groups: dict[tuple, list[Path]] = {}
    for path in sorted(destination.glob("sub-*/**/*_desc-preproc_dwi.nii.gz")):
        entities_for_path = entities(path)
        if "acq" not in entities_for_path:
            continue
        key = (path.parent, tuple(sorted((k, v) for k, v in entities_for_path.items() if k != "acq")))
        groups.setdefault(key, []).append(path)
    combined = []
    for files in groups.values():
        if len(files) < 2:
            continue
        # A combination is allowed only when its staged paths map to QSIPrep
        # products with explicit acquisition entities and matching metadata.
        combined.append(combine_acquisition_group(files, destination, bids_root))
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="completed QSIPrep derivatives directory")
    parser.add_argument("destination", type=Path, help="QSIRecon-only derivative view")
    parser.add_argument("--bids-dir", type=Path,
                        help="raw BIDS input, used when QSIPrep omits acquisition JSON sidecars")
    parser.add_argument("--no-combine-acquisitions", action="store_true",
                        help="keep separate QSIPrep acq-* outputs")
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    bids_root = args.bids_dir.resolve() if args.bids_dir else None
    manifest = destination.with_name(destination.name + "_manifest.json")

    if destination.exists():
        if not manifest.is_file():
            raise FileExistsError(f"Overlay exists without its manifest: {destination}")
        metadata = json.loads(manifest.read_text())
        if metadata.get("Version") != 3:
            raise ValueError(f"Overlay manifest is outdated; use a new work directory: {manifest}")
        if metadata.get("Source") != str(source) or metadata.get("BIDSDir") != (
                str(bids_root) if bids_root else None):
            raise ValueError(f"Overlay inputs mismatch recorded in {manifest}")
        print(json.dumps(metadata, indent=2))
        return 0

    if not (source / "dataset_description.json").is_file():
        raise FileNotFoundError(f"QSIPrep dataset description missing under {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, copy_function=link_or_copy, symlinks=True)

    combined = [] if args.no_combine_acquisitions else combine_compatible_acquisitions(destination, bids_root)
    moved = []
    for dwi in sorted(destination.glob("sub-*/**/*_desc-preproc_dwi.nii.gz")):
        mask_name = dwi.name.replace("_desc-preproc_dwi.nii.gz", "_desc-brain_mask.nii.gz")
        candidates = sorted(destination.glob(f"**/{mask_name}"))
        if len(candidates) != 1:
            raise ValueError(f"Expected one mask for {dwi}, found {len(candidates)}")
        source_mask = candidates[0]
        target_mask = dwi.with_name(mask_name)
        dwi_image = nib.load(str(dwi))
        mask_image = nib.load(str(source_mask))
        if mask_image.shape != dwi_image.shape[:3]:
            raise ValueError(f"Mask shape mismatch for {dwi}: {mask_image.shape}")
        if not np.allclose(mask_image.affine, dwi_image.affine, atol=1e-4):
            raise ValueError(f"Mask affine mismatch for {dwi}")
        if source_mask != target_mask:
            if target_mask.exists():
                raise FileExistsError(f"Refusing to overwrite existing mask: {target_mask}")
            os.replace(source_mask, target_mask)
            moved.append({"From": str(source_mask.relative_to(destination)),
                          "To": str(target_mask.relative_to(destination))})

    if not moved and not combined:
        raise ValueError(f"No misplaced DWI masks found under {source}; refusing empty overlay")
    metadata = {"Source": str(source), "BIDSDir": str(bids_root) if bids_root else None,
                "DwiMasksRelocated": moved,
                "CombinedAcquisitions": combined,
                "Mode": "hardlink with copy fallback", "Version": 3}
    manifest.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
