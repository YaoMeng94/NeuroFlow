"""Finalize dataset-level BIDS metadata and fieldmap references."""

from __future__ import annotations

import argparse
import json
import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


def _target_files(subject_root: Path, description: dict[str, Any]) -> list[Path]:
    datatype = description["datatype"]
    suffix = description["suffix"]
    entities = description.get("custom_entities", "").split("_")
    matches = []
    for sidecar in (subject_root / datatype).glob("**/*.json"):
        tokens = sidecar.stem.split("_")
        if tokens[-1] != suffix or not all(entity in tokens for entity in entities if entity):
            continue
        metadata = json.loads(sidecar.read_text())
        criteria = description.get("criteria", {})
        if any(metadata.get(key) != value for key, value in criteria.items()):
            continue
        matches.append(sidecar)
    return sorted(matches)


def finalize_bids(output: Path, participant: str, session: str, config: dict[str, Any]) -> None:
    output = output.expanduser().resolve()
    subject_root = output / f"sub-{participant}"
    if session:
        subject_root /= f"ses-{session}"
    if not subject_root.is_dir():
        raise ValueError(f"Converted subject folder is missing: {subject_root}")

    dataset_description = output / "dataset_description.json"
    if not dataset_description.exists():
        try:
            dcm2bids_version = version("dcm2bids")
        except PackageNotFoundError:
            dcm2bids_version = "unknown"
        dataset_description.write_text(json.dumps({
            "Name": "NeuroFlow converted dataset",
            "BIDSVersion": "1.9.0",
            "GeneratedBy": [{"Name": "dcm2bids", "Version": dcm2bids_version}],
        }, indent=2) + "\n")

    identifiers: dict[str, list[Path]] = {}
    for description in config.get("descriptions", []):
        identifier = description.get("id")
        if identifier:
            targets = _target_files(subject_root, description)
            if not targets:
                raise ValueError(f"No BIDS files found for mapping ID {identifier!r}")
            identifiers[identifier] = targets

    for description in config.get("descriptions", []):
        if description.get("datatype") != "fmap" or not description.get("sidecar_changes", {}).get("IntendedFor"):
            continue
        fieldmaps = _target_files(subject_root, description)
        if not fieldmaps:
            raise ValueError(f"No fieldmap sidecar found for mapping ID {description.get('id')!r}")
        intended = description["sidecar_changes"]["IntendedFor"]
        if isinstance(intended, str):
            intended = [intended]
        paths = []
        for identifier in intended:
            if identifier not in identifiers:
                raise ValueError(f"Unresolved IntendedFor mapping ID: {identifier}")
            participant_root = output / f"sub-{participant}"
            paths.extend(
                target.relative_to(participant_root).as_posix().replace(".json", ".nii.gz")
                for target in identifiers[identifier]
            )
        for fieldmap_json in fieldmaps:
            metadata = json.loads(fieldmap_json.read_text())
            metadata["IntendedFor"] = paths
            fieldmap_json.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")

    for datatype, suffix in (("fmap", "epi"), ("dwi", "sbref")):
        for image in (subject_root / datatype).glob(f"*_{suffix}.nii*"):
            stem = image.name.removesuffix(".nii.gz").removesuffix(".nii")
            for extension in (".bval", ".bvec"):
                gradient = image.with_name(stem + extension)
                if gradient.exists():
                    gradient.unlink()

    temporary_root = output / "tmp_dcm2bids"
    archive_root = output.parent / "dcm2bids_intermediates" / output.name
    temporary_subject = temporary_root / f"sub-{participant}"
    if temporary_subject.is_dir():
        archive_subject = archive_root / temporary_subject.name
        archive_subject.parent.mkdir(parents=True, exist_ok=True)
        if archive_subject.exists():
            raise ValueError(f"Refusing to replace existing conversion intermediates: {archive_subject}")
        shutil.move(str(temporary_subject), str(archive_subject))
    temporary_logs = temporary_root / "log"
    if temporary_logs.is_dir():
        archive_logs = archive_root / "log"
        for log_file in temporary_logs.glob(f"sub-{participant}_*.log"):
            archive_logs.mkdir(parents=True, exist_ok=True)
            destination = archive_logs / log_file.name
            if destination.exists():
                raise ValueError(f"Refusing to replace existing conversion log: {destination}")
            shutil.move(str(log_file), str(destination))
    if temporary_root.is_dir():
        for intermediate in sorted(temporary_root.rglob("*")):
            if not intermediate.is_file():
                continue
            destination = archive_root / intermediate.relative_to(temporary_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError(f"Refusing to replace existing conversion intermediate: {destination}")
            shutil.move(str(intermediate), str(destination))
        for directory in sorted(temporary_root.rglob("*"), reverse=True):
            if directory.is_dir():
                directory.rmdir()
        temporary_root.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("participant")
    parser.add_argument("session")
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        finalize_bids(args.output, args.participant, args.session, config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
