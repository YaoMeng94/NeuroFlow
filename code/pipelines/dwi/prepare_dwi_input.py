#!/usr/bin/env python3
"""Create a non-destructive QSIPrep input copy with explicit DWI fieldmap links."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
from nibabel.processing import resample_from_to


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--participant-label", nargs="+")
    parser.add_argument("--session-id", nargs="+")
    parser.add_argument(
        "--b0-policy",
        choices=("auto", "include", "exclude"),
        default="auto",
        help="whether b0-only *_dwi acquisitions enter QSIPrep (auto excludes when fmap EPI exists)",
    )
    parser.add_argument(
        "--geometry-policy",
        choices=("auto", "preserve", "resample"),
        default="auto",
        help="handle differing DWI voxel grids (auto resamples only when needed)",
    )
    parser.add_argument(
        "--dwi-b0-sdc",
        choices=("auto", "none"),
        default="auto",
        help="promote a reverse-PE b0-only DWI to a temporary fmap EPI for DWI TOPUP",
    )
    parser.add_argument(
        "--dwi-b0-source",
        type=Path,
        help="optional source-relative or absolute b0-only DWI to promote as the reverse-PE fmap",
    )
    parser.add_argument(
        "--overlay-path",
        type=Path,
        help="path for the provenance overlay (default: uniquely named beside the output directory)",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def subject_label(path: Path) -> str:
    return next((part for part in path.parts if part.startswith("sub-")), "")


def session_label(path: Path) -> str:
    return next((part for part in path.parts if part.startswith("ses-")), "")


def entity_key(path: Path) -> tuple[str, str]:
    """Return the subject/session acquisition group for a BIDS path."""
    return subject_label(path), session_label(path)


def entity_root(root: Path, path: Path) -> Path:
    subject, session = entity_key(path)
    return root / subject / session if session else root / subject


def promoted_fmap_name(path: Path) -> str:
    subject, session = entity_key(path)
    match = re.search(r"(?:^|_)dir-([A-Za-z0-9]+)(?:_|$)", path.name)
    direction = match.group(1) if match else None
    if direction is None:
        match = re.search(r"(?:AP|PA)(?=_dwi\.nii\.gz$)", path.name)
        direction = match.group(0) if match else "reverse"
    prefix = "_".join(part for part in (subject, session) if part)
    return f"{prefix}_acq-b0_dir-{direction}_epi.nii.gz"


def sidecar(path: Path, suffix: str) -> Path:
    return path.with_name(path.name.replace(".nii.gz", suffix))


def is_all_zero_bvals(path: Path) -> bool:
    try:
        values = [float(value) for value in path.read_text().split()]
    except (OSError, ValueError):
        return False
    return bool(values) and all(value == 0 for value in values)


def is_b0_candidate(path: Path) -> bool:
    """Recognize a b0-only DWI input, including sidecar-free named b0 scans."""
    bval = sidecar(path, ".bval")
    return is_all_zero_bvals(bval) if bval.exists() else "b0" in path.name.lower()


def opposite_pe(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return False
    first_axis, first_sign = first[0], -1 if first.endswith("-") else 1
    second_axis, second_sign = second[0], -1 if second.endswith("-") else 1
    return first_axis == second_axis and first_sign != second_sign


def copy_tree(source: Path, output: Path, overwrite: bool) -> None:
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise SystemExit(f"Output is non-empty; use --overwrite: {output}")
    if output.exists() and overwrite:
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    # QSIPrep only needs dataset metadata and structural, diffusion, and
    # fieldmap inputs. Avoid duplicating unrelated functional/physiology data
    # into the processing copy, which can be several gigabytes per subject.
    for source_path in source.rglob("*"):
        if not source_path.is_file():
            continue
        relative = source_path.relative_to(source)
        if len(relative.parts) > 1 and not any(
            part in {"anat", "dwi", "fmap"} for part in relative.parts
        ):
            continue
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)


def prune_unavailable_fmap_targets(root: Path) -> tuple[list[str], list[str]]:
    """Keep fieldmaps scoped to modalities present in the DWI input copy."""
    excluded: list[str] = []
    updated: list[str] = []
    for metadata_path in sorted(root.glob("sub-*/**/fmap/*.json")):
        metadata = json.loads(metadata_path.read_text())
        intended_for = metadata.get("IntendedFor")
        if not intended_for:
            continue
        targets = [intended_for] if isinstance(intended_for, str) else intended_for
        subject_root = next(
            (parent for parent in metadata_path.parents if parent.name.startswith("sub-")),
            None,
        )
        if subject_root is None:
            continue
        present = []
        for target in targets:
            relative = str(target).removeprefix("bids::")
            if relative.startswith(f"{subject_root.name}/"):
                relative = relative[len(subject_root.name) + 1 :]
            if (subject_root / relative).is_file():
                present.append(target)
        if present:
            if present != targets:
                metadata["IntendedFor"] = present
                metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
                updated.append(str(metadata_path.relative_to(root)))
            continue

        image_path = metadata_path.with_name(metadata_path.name.removesuffix(".json") + ".nii.gz")
        for candidate in (metadata_path, image_path):
            if candidate.exists():
                candidate.unlink()
        excluded.append(str(image_path.relative_to(root)))
    return excluded, updated


def selected_subjects(root: Path, labels: list[str] | None) -> set[str] | None:
    if not labels:
        return None
    return {label if label.startswith("sub-") else f"sub-{label}" for label in labels}


def selected_sessions(labels: list[str] | None) -> set[str] | None:
    if not labels:
        return None
    return {label if label.startswith("ses-") else f"ses-{label}" for label in labels}


def _group_by_entity(paths: list[Path]) -> dict[tuple[str, str], list[Path]]:
    grouped: dict[tuple[str, str], list[Path]] = {}
    for path in paths:
        grouped.setdefault(entity_key(path), []).append(path)
    for subject_paths in grouped.values():
        subject_paths.sort()
    return grouped


def orientation_rotation(affine: np.ndarray) -> np.ndarray:
    """Return the closest proper rotation for the voxel-to-world matrix."""
    matrix = affine[:3, :3]
    matrix = matrix / np.linalg.norm(matrix, axis=0, keepdims=True)
    left, _, right = np.linalg.svd(matrix)
    rotation = left @ right
    if np.linalg.det(rotation) < 0:
        left[:, -1] *= -1
        rotation = left @ right
    return rotation


def rotate_bvecs(path: Path, source_affine: np.ndarray, target_affine: np.ndarray) -> None:
    bvec = sidecar(path, ".bvec")
    if not bvec.exists():
        return
    rows = [line.split() for line in bvec.read_text().splitlines() if line.strip()]
    if len(rows) != 3:
        return
    values = np.asarray(rows, dtype=float)
    rotated = (
        orientation_rotation(target_affine).T
        @ orientation_rotation(source_affine)
        @ values
    )
    norms = np.linalg.norm(rotated, axis=0)
    nonzero = norms > 1e-8
    rotated[:, nonzero] /= norms[nonzero]
    bvec.write_text(
        "\n".join(" ".join(f"{value:.8f}" for value in row) for row in rotated) + "\n"
    )


def resample_to_reference(path: Path, reference: nib.spatialimages.SpatialImage) -> None:
    source = nib.load(str(path))
    source_affine = source.affine.copy()
    # Preserve the source time/direction dimension while replacing only the
    # spatial grid. Passing a 3D target to nibabel for a 4D source creates an
    # incompatible affine dimensionality (5x5 versus 4x4).
    target = (reference.shape[:3] + source.shape[3:], reference.affine)
    resampled = resample_from_to(source, target, order=1, mode="constant", cval=0.0)
    data = np.asanyarray(resampled.dataobj).astype(np.float32, copy=False)
    output = nib.Nifti1Image(data, reference.affine, header=resampled.header)
    output.set_qform(reference.affine, code=1)
    output.set_sform(reference.affine, code=1)
    nib.save(output, str(path))
    rotate_bvecs(path, source_affine, reference.affine)


def promote_b0_to_fmap(
    source_dwi: Path,
    output: Path,
    reference: nib.spatialimages.SpatialImage,
    geometry_policy: str,
) -> bool:
    """Create a BIDS EPI fmap from a reverse-PE b0-only DWI acquisition."""
    source_image = nib.load(str(source_dwi))
    if source_image.shape[:3] != reference.shape[:3] or not np.allclose(
        source_image.affine, reference.affine, atol=1e-4
    ):
        if geometry_policy == "preserve":
            raise SystemExit(
                f"Reverse-PE b0 geometry differs from directional DWI: {source_dwi}"
            )
        target = (reference.shape[:3] + source_image.shape[3:], reference.affine)
        source_image = resample_from_to(source_image, target, order=1, mode="constant", cval=0.0)
        data = np.asanyarray(source_image.dataobj).astype(np.float32, copy=False)
        image = nib.Nifti1Image(data, reference.affine, header=source_image.header)
        image.set_qform(reference.affine, code=1)
        image.set_sform(reference.affine, code=1)
        resampled = True
    else:
        image = source_image
        resampled = False
    output.parent.mkdir(parents=True, exist_ok=True)
    nib.save(image, str(output))
    source_json = sidecar(source_dwi, ".json")
    metadata: dict[str, Any] = json.loads(source_json.read_text())
    for key in ("IntendedFor", "B0FieldSource", "B0FieldIdentifier"):
        metadata.pop(key, None)
    metadata["IntendedFor"] = []
    output_json = sidecar(output, ".json")
    output_json.write_text(json.dumps(metadata, indent=2) + "\n")
    return resampled


def main() -> int:
    config = parse_args()
    source = config.source.resolve()
    output = config.output.resolve()
    if not source.is_dir():
        raise SystemExit(f"Source BIDS directory does not exist: {source}")
    if source == output:
        raise SystemExit("Processing copy must be different from the source BIDS directory")

    default_overlay = output.parent / f"{output.name}_dwi_processing_overlay.json"
    overlay_path = (config.overlay_path or default_overlay).resolve()
    if overlay_path in {source, output}:
        raise SystemExit("Provenance overlay must be outside the source and processing BIDS directories")
    stale_overlay = overlay_path
    if stale_overlay.exists() and config.overwrite:
        stale_overlay.unlink()
    copy_tree(source, output, config.overwrite)
    excluded_unavailable_fmaps, updated_fmap_targets = prune_unavailable_fmap_targets(output)
    selected = selected_subjects(source, config.participant_label)
    sessions = selected_sessions(config.session_id)
    all_dwi_files = sorted(output.glob("sub-*/**/dwi/*_dwi.nii.gz"))
    all_dwi_files = [
        path
        for path in all_dwi_files
        if (selected is None or subject_label(path) in selected)
        and (sessions is None or session_label(path) in sessions)
    ]
    epi_fieldmaps_by_entity = {
        entity: bool(list((entity_root(output, path) / "fmap").glob("*_epi.nii.gz")))
        for entity in {entity_key(path) for path in all_dwi_files}
        for path in [next(item for item in all_dwi_files if entity_key(item) == entity)]
    }
    directional_dwi: list[Path] = []
    b0_only_dwi: list[Path] = []
    for path in all_dwi_files:
        if is_b0_candidate(path):
            b0_only_dwi.append(path)
        else:
            directional_dwi.append(path)

    selected_b0_sources: list[Path] = []
    excluded_epi_fieldmaps: list[str] = []
    promoted_b0_fieldmaps: list[str] = []
    resampled_b0_fieldmaps: list[str] = []
    if config.dwi_b0_source is not None:
        requested_source = config.dwi_b0_source
        if requested_source.is_absolute():
            try:
                requested_source = output / requested_source.relative_to(source)
            except ValueError:
                pass
        else:
            requested_source = output / requested_source
        requested_source = requested_source.resolve()
        if requested_source not in b0_only_dwi:
            raise SystemExit(
                f"--dwi-b0-source is not a selected all-zero b0-only DWI: {config.dwi_b0_source}"
            )
        selected_b0_sources = [requested_source]
    elif config.dwi_b0_sdc == "auto" and b0_only_dwi and directional_dwi:
        directional_by_entity: dict[tuple[str, str], list[Path]] = {}
        b0_by_entity: dict[tuple[str, str], list[Path]] = {}
        for path in directional_dwi:
            directional_by_entity.setdefault(entity_key(path), []).append(path)
        for path in b0_only_dwi:
            b0_by_entity.setdefault(entity_key(path), []).append(path)
        for entity, subject_dwi in directional_by_entity.items():
            subject_b0 = b0_by_entity.get(entity, [])
            if not subject_b0:
                continue
            directional_metadata = [
                json.loads(sidecar(path, ".json").read_text()) for path in subject_dwi
            ]
            directional_pe = {
                metadata.get("PhaseEncodingDirection") for metadata in directional_metadata
            }
            directional_readout = {
                metadata.get("TotalReadoutTime") for metadata in directional_metadata
            }
            candidates = []
            for path in subject_b0:
                metadata = json.loads(sidecar(path, ".json").read_text())
                if (
                    any(opposite_pe(pe, metadata.get("PhaseEncodingDirection")) for pe in directional_pe)
                    and metadata.get("TotalReadoutTime") in directional_readout
                ):
                    candidates.append(path)
            # One reverse-PE acquisition per subject is sufficient; deterministic
            # selection keeps reruns reproducible when duplicate b0 sets exist.
            selected_b0_sources.extend(sorted(candidates)[:1])

    promoted_entities = {entity_key(path) for path in selected_b0_sources}
    excluded_entities = {
        entity
        for entity in epi_fieldmaps_by_entity
        if config.b0_policy == "exclude"
        or (config.b0_policy == "auto" and (epi_fieldmaps_by_entity[entity] or entity in promoted_entities))
    }
    excluded_b0_dwi: list[str] = []
    dwi_files = []
    for path in all_dwi_files:
        if path in selected_b0_sources:
            # Keep the source available until it has been promoted to fmap/*_epi.
            continue
        if entity_key(path) in excluded_entities and "b0" in path.name.lower():
            excluded_b0_dwi.append(str(path.relative_to(output)))
            for suffix in (".nii.gz", ".json", ".bval", ".bvec"):
                candidate = path.with_name(path.name.replace(".nii.gz", suffix))
                if candidate.exists():
                    candidate.unlink()
            # A b0-only SBRef belongs to the excluded b0 acquisition. Keep
            # the processing copy free of orphan references when another b0
            # scan was selected for reverse-PE TOPUP.
            sbref = path.with_name(path.name.replace("_dwi.nii.gz", "_sbref.nii.gz"))
            for candidate in (sbref, sidecar(sbref, ".json")):
                if candidate.exists():
                    candidate.unlink()
            continue
        dwi_files.append(path)
    created_zero_sidecars: list[str] = []
    fieldmap_updates: list[str] = []
    resampled_dwi: list[str] = []

    if selected_b0_sources:
        if not dwi_files:
            raise SystemExit("Cannot promote a reverse-PE b0 without a directional DWI reference")
        directional_references = {
            entity: nib.load(str(paths[0]))
            for entity, paths in _group_by_entity(dwi_files).items()
        }
        for path in selected_b0_sources:
            fmap_path = entity_root(output, path) / "fmap" / promoted_fmap_name(path)
            was_resampled = promote_b0_to_fmap(
                path,
                fmap_path,
                directional_references[entity_key(path)],
                config.geometry_policy,
            )
            relative_fmap = str(fmap_path.relative_to(output))
            fmap_metadata = json.loads(sidecar(fmap_path, ".json").read_text())
            fmap_metadata["IntendedFor"] = [
                str(dwi.relative_to(entity_root(output, path)))
                for dwi in dwi_files
                if entity_key(dwi) == entity_key(path)
            ]
            sidecar(fmap_path, ".json").write_text(json.dumps(fmap_metadata, indent=2) + "\n")
            promoted_b0_fieldmaps.append(relative_fmap)
            if was_resampled:
                resampled_b0_fieldmaps.append(relative_fmap)
            excluded_b0_dwi.append(str(path.relative_to(output)))
            for suffix in (".nii.gz", ".json", ".bval", ".bvec"):
                candidate = path.with_name(path.name.replace(".nii.gz", suffix))
                if candidate.exists():
                    candidate.unlink()
            sbref = path.with_name(path.name.replace("_dwi.nii.gz", "_sbref.nii.gz"))
            for candidate in (sbref, sidecar(sbref, ".json")):
                if candidate.exists():
                    candidate.unlink()
        # Dedicated SE-EPI maps in the source are retained for BOLD, but are
        # excluded from this DWI-only processing copy to avoid mixed readouts.
        for entity in sorted({entity_key(path) for path in selected_b0_sources}):
            fmap_dir = entity_root(output, next(path for path in selected_b0_sources if entity_key(path) == entity)) / "fmap"
            for fmap in sorted(fmap_dir.glob("*_epi.nii.gz")):
                if str(fmap.relative_to(output)) in promoted_b0_fieldmaps:
                    continue
                relative_fmap = str(fmap.relative_to(output))
                for suffix in (".nii.gz", ".json"):
                    candidate = fmap.with_name(fmap.name.replace(".nii.gz", suffix))
                    if candidate.exists():
                        candidate.unlink()
                excluded_epi_fieldmaps.append(relative_fmap)

    if dwi_files:
        for entity, subject_dwi in _group_by_entity(dwi_files).items():
            reference = nib.load(str(subject_dwi[0]))
            geometry_differs = any(
                nib.load(str(path)).shape[:3] != reference.shape[:3]
                or not np.allclose(nib.load(str(path)).affine, reference.affine, atol=1e-4)
                for path in subject_dwi[1:]
            )
            if geometry_differs and config.geometry_policy == "preserve":
                raise SystemExit(
                    f"DWI acquisitions have different voxel grids for {entity}; "
                    "use --geometry-policy auto or resample"
                )
            if geometry_differs and config.geometry_policy in {"auto", "resample"}:
                candidates = subject_dwi[1:]
            else:
                candidates = []
            for path in candidates:
                source_image = nib.load(str(path))
                if source_image.shape[:3] != reference.shape[:3] or not np.allclose(
                    source_image.affine, reference.affine, atol=1e-4
                ):
                    resample_to_reference(path, reference)
                    resampled_dwi.append(str(path.relative_to(output)))

    for path in dwi_files:
        bval = sidecar(path, ".bval")
        bvec = sidecar(path, ".bvec")
        if "b0" in path.name.lower() and (not bval.exists() or not bvec.exists()):
            volumes = nib.load(str(path)).shape[3]
            bval.write_text(" ".join("0" for _ in range(volumes)) + "\n")
            bvec.write_text("\n".join(" ".join("0" for _ in range(volumes)) for _ in range(3)) + "\n")
            created_zero_sidecars.extend([str(bval.relative_to(output)), str(bvec.relative_to(output))])

    # Preserve each fmap's explicit BIDS IntendedFor scope. Guessing by
    # subject/session attaches BOLD-only fieldmaps to DWI and can mix distinct
    # readout geometries into a QSIPrep TOPUP group.

    notes = [
        "The source BIDS tree is not modified.",
        "All-zero b0 sidecars are created only in the processing copy when absent, because QSIPrep requires a complete DWI sidecar contract.",
        "Fieldmaps targeting modalities omitted from the DWI processing copy are removed or have IntendedFor restricted to retained files.",
    ]
    if selected_b0_sources:
        notes.append(
            "A metadata-compatible reverse-PE b0 is represented as fmap/*_epi without bval/bvec; dedicated EPI fieldmaps are removed from the DWI processing copy to avoid mixed readout geometry."
        )
    else:
        notes.append(
            "Existing fieldmap metadata is preserved and DWI targets are appended to IntendedFor."
        )

    manifest = {
        "SchemaVersion": "1.0.0",
        "SourceBIDS": str(source),
        "ProcessingBIDS": str(output),
        "Participants": sorted({subject_label(path) for path in dwi_files}),
        "Sessions": sorted({session_label(path) for path in dwi_files if session_label(path)}),
        "DWIForFieldmapIntendedFor": [str(path.relative_to(output)) for path in dwi_files],
        "ExcludedB0DWI": excluded_b0_dwi,
        "DWIReversePEB0Sources": [str(path.relative_to(output)) for path in selected_b0_sources],
        "B0Policy": config.b0_policy,
        "DWIB0SDC": config.dwi_b0_sdc,
        "GeometryPolicy": config.geometry_policy,
        "ResampledDWI": resampled_dwi,
        "DWIReversePEB0Fieldmaps": promoted_b0_fieldmaps,
        "ResampledDWIReversePEB0Fieldmaps": resampled_b0_fieldmaps,
        "ExcludedEPIFieldmaps": excluded_epi_fieldmaps,
        "ExcludedUnavailableTargetFieldmaps": excluded_unavailable_fmaps,
        "UpdatedFieldmapTargets": updated_fmap_targets,
        "CreatedZeroBvalBvec": created_zero_sidecars,
        "UpdatedFieldmapJSON": fieldmap_updates,
        "Notes": notes,
    }
    # Keep provenance beside the processing tree so the BIDS validator sees a
    # clean dataset root. The source BIDS tree remains untouched.
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Processing BIDS copy: {output}")
    print(f"DWI files: {len(dwi_files)}")
    print(f"Created zero b0 sidecars: {len(created_zero_sidecars)}")
    print(f"Updated fieldmap JSON files: {len(fieldmap_updates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
