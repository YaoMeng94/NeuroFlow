"""Rule-driven, source-preserving DICOM series sorting."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pydicom


DEFAULT_RULES: dict[str, Any] = {
    "version": 1,
    "description": "Add rules for this scanner protocol. Regexes are case-insensitive by default.",
    "rules": [],
}

_TAGS = {
    "PatientID": "PatientID",
    "SeriesDescription": "SeriesDescription",
    "ProtocolName": "ProtocolName",
    "Modality": "Modality",
    "SequenceName": "SequenceName",
}


@dataclass(frozen=True)
class DicomFile:
    path: Path
    study_uid: str
    series_uid: str
    values: dict[str, str]


def load_rules(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read rule file {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("rules"), list):
        raise ValueError("Rule file must be a version 1 JSON object with a rules list")
    for index, rule in enumerate(data["rules"], 1):
        if not isinstance(rule, dict) or not isinstance(rule.get("name"), str) or not rule["name"].strip():
            raise ValueError(f"Rule {index} needs a non-empty name")
        if not isinstance(rule.get("category"), str) or not rule["category"].strip():
            raise ValueError(f"Rule {index} needs a non-empty category")
        match = rule.get("match")
        if not isinstance(match, dict) or not match:
            raise ValueError(f"Rule {index} needs at least one match field")
        unknown = set(match) - set(_TAGS)
        if unknown:
            raise ValueError(f"Rule {index} uses unsupported DICOM fields: {', '.join(sorted(unknown))}")
        for field, pattern in match.items():
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"Rule {index} field {field} needs a non-empty regex")
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"Rule {index} has invalid regex for {field}: {exc}") from exc
    return data


def write_rule_template(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to replace existing rule file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_RULES, indent=2) + "\n", encoding="utf-8")


def _read_file(path: Path) -> DicomFile | None:
    try:
        ds = pydicom.dcmread(
            path,
            stop_before_pixels=True,
            force=False,
            specific_tags=[
                "StudyInstanceUID", "SeriesInstanceUID", "PatientID", "SeriesDescription",
                "ProtocolName", "Modality", "SequenceName", "SeriesNumber",
            ],
        )
    except Exception:
        return None
    study = str(getattr(ds, "StudyInstanceUID", ""))
    series = str(getattr(ds, "SeriesInstanceUID", ""))
    if not study or not series:
        return None
    values = {key: str(getattr(ds, tag, "")) for key, tag in _TAGS.items()}
    values["SeriesNumber"] = str(getattr(ds, "SeriesNumber", ""))
    return DicomFile(path=path, study_uid=study, series_uid=series, values=values)


def _dicomdir_records(
    source: Path,
    paths: list[Path],
    progress_callback: Callable[[str], None] | None = None,
) -> list[DicomFile] | None:
    """Use a complete DICOMDIR index when it covers every source file."""
    source = source.resolve()
    dicomdir = next((candidate for candidate in (source / "DICOMDIR", source.parent / "DICOMDIR")
                     if candidate.is_file()), None)
    if dicomdir is None:
        return None
    try:
        index = pydicom.dcmread(dicomdir, stop_before_pixels=True, force=False)
    except Exception:
        return None

    series_meta: dict[tuple[str, str], dict[str, str]] = {}
    series_files: dict[tuple[str, str], list[Path]] = defaultdict(list)
    expected = {path.resolve() for path in paths if path.name.casefold() != "dicomdir"}
    # DICOMDIR File IDs are relative to the original export root. When users
    # copy a DICOMDIR beside a selected subfolder, rebase only if every ID maps
    # uniquely to a file in that selected source.
    source_by_suffix: dict[tuple[str, ...], set[Path]] = defaultdict(set)
    max_suffix = 0
    for path in expected:
        try:
            relative_parts = path.relative_to(source).parts
        except ValueError:
            continue
        max_suffix = max(max_suffix, len(relative_parts))
        folded_parts = tuple(part.casefold() for part in relative_parts)
        for length in range(1, len(folded_parts) + 1):
            source_by_suffix[folded_parts[-length:]].add(path)

    study_uid = ""
    current_series: tuple[str, str] | None = None
    referenced: list[Path] = []
    try:
        for entry in index.DirectoryRecordSequence:
            kind = str(getattr(entry, "DirectoryRecordType", ""))
            if kind == "PATIENT":
                study_uid = ""
                current_series = None
            elif kind == "STUDY":
                study_uid = str(getattr(entry, "StudyInstanceUID", ""))
                current_series = None
            elif kind == "SERIES":
                series_uid = str(getattr(entry, "SeriesInstanceUID", ""))
                current_series = (study_uid, series_uid)
                series_meta[current_series] = {
                    "SeriesDescription": str(getattr(entry, "SeriesDescription", "")),
                    "ProtocolName": "",
                    "Modality": str(getattr(entry, "Modality", "")),
                    "SequenceName": "",
                    "SeriesNumber": str(getattr(entry, "SeriesNumber", "")),
                }
            elif kind in {"IMAGE", "RAW DATA", "SR DOCUMENT", "PRESENTATION"} and current_series:
                file_id = getattr(entry, "ReferencedFileID", None)
                if file_id:
                    parts = tuple(map(str, file_id))
                    path = dicomdir.parent.joinpath(*parts).resolve()
                    if path not in expected:
                        path = None
                        folded_parts = tuple(part.casefold() for part in parts)
                        for length in range(min(len(folded_parts), max_suffix), 0, -1):
                            matches = source_by_suffix.get(folded_parts[-length:], set())
                            if matches:
                                if len(matches) != 1:
                                    return None
                                path = next(iter(matches))
                                break
                    if path is None:
                        return None
                    referenced.append(path)
                    series_files[current_series].append(path)
    except Exception:
        return None

    indexed = set(referenced)
    if len(referenced) != len(indexed) or indexed != expected or not series_meta:
        return None
    if any(not path.is_file() for path in indexed):
        return None

    # The DICOMDIR carries reliable study/series membership but can omit ProtocolName.
    # Read one representative header per series to complete the matching fields.
    for index, (key, files) in enumerate(series_files.items(), 1):
        representative = _read_file(files[0])
        if not representative or (representative.study_uid, representative.series_uid) != key:
            return None
        series_meta[key] = representative.values
        if progress_callback and (index == len(series_files) or index % 10 == 0):
            progress_callback(f"Reading series headers from DICOMDIR: {index} of {len(series_files)}…")

    return [
        DicomFile(path=path, study_uid=key[0], series_uid=key[1], values=series_meta[key])
        for key, files in series_files.items()
        for path in files
    ]


def _safe_name(value: str, fallback: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return (clean[:72] or fallback)


def _series_order(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, value.casefold())


def _subject_labels(records: dict[tuple[str, str], list[DicomFile]]) -> dict[str, str]:
    """Use the scanner's PatientID as the BIDS label; never invent ordinals."""
    patient_ids: dict[str, set[str]] = defaultdict(set)
    for (study_uid, _), files in records.items():
        patient_ids[study_uid].update(
            file.values.get("PatientID", "").strip() for file in files
            if file.values.get("PatientID", "").strip()
        )
    labels: dict[str, str] = {}
    owners: dict[str, str] = {}
    for study_uid, ids in patient_ids.items():
        if len(ids) != 1:
            reason = "missing" if not ids else "inconsistent"
            raise ValueError(f"Cannot assign a subject label: PatientID is {reason} for one DICOM study")
        raw_id = next(iter(ids))
        label = re.sub(r"[^A-Za-z0-9]", "", raw_id)
        if not label:
            raise ValueError("Cannot assign a BIDS subject label: DICOM PatientID has no letters or digits")
        previous = owners.get(label)
        if previous is not None and previous != raw_id:
            raise ValueError("Different DICOM PatientID values collapse to the same BIDS label after cleanup")
        owners[label] = raw_id
        labels[study_uid] = f"sub-{label}"
    missing = set(study_uid for study_uid, _ in records) - set(labels)
    if missing:
        raise ValueError("Cannot assign BIDS subject labels: DICOM PatientID is missing")
    return labels


def inspect_dicom(
    source: Path,
    rules: dict[str, Any],
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"DICOM input folder does not exist: {source}")
    records: dict[tuple[str, str], list[DicomFile]] = defaultdict(list)
    # Prune generated output folders before walking them. Filtering their files
    # after Path.rglob has traversed them can make a preview appear hung when a
    # scanner folder also contains a large sorted copy or BIDS dataset.
    paths: list[Path] = []

    def walk_error(error: OSError) -> None:
        raise error

    for directory, child_dirs, filenames in os.walk(source, topdown=True, onerror=walk_error):
        child_dirs[:] = sorted(
            name for name in child_dirs
            if not name.casefold().endswith(("_sorted", "_bids"))
        )
        for name in sorted(filenames):
            if name.startswith("._") or name == ".DS_Store":
                continue
            paths.append(Path(directory) / name)
            if progress_callback and len(paths) % 1000 == 0:
                progress_callback(f"Scanning scanner folder: {len(paths):,} files found…")
    if progress_callback:
        progress_callback(f"Checking DICOMDIR coverage for {len(paths):,} files…")

    indexed_records = _dicomdir_records(source, paths, progress_callback)
    if indexed_records is not None:
        for record in indexed_records:
            records[(record.study_uid, record.series_uid)].append(record)
    else:
        # Header reads are I/O bound; a small thread pool helps on large exports
        # without copying pixel data or creating platform-specific process workers.
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
            for index, record in enumerate(pool.map(_read_file, paths, chunksize=8), 1):
                if record:
                    records[(record.study_uid, record.series_uid)].append(record)
                if progress_callback and (index % 100 == 0 or index == len(paths)):
                    progress_callback(f"Reading DICOM headers: {index:,} of {len(paths):,} files…")

    study_ids = _subject_labels(records)
    result = []
    used_targets: set[tuple[str, str, str]] = set()
    for (study_uid, series_uid), files in sorted(records.items(), key=lambda item: (
        study_ids[item[0][0]], _series_order(item[1][0].values["SeriesNumber"]), item[0][1]
    )):
        values = files[0].values
        matches = []
        for rule in rules["rules"]:
            if all(re.search(pattern, values.get(field, ""), re.IGNORECASE) for field, pattern in rule["match"].items()):
                matches.append(rule)
        if len(matches) == 1:
            category = _safe_name(matches[0]["category"], "sorted")
            rule_name = matches[0]["name"]
            status = "matched"
        elif not matches:
            category, rule_name, status = "_unmatched", "", "unmatched"
        else:
            category, rule_name, status = "_review_multiple_matches", ", ".join(x["name"] for x in matches), "ambiguous"
        description = _safe_name(values.get("SeriesDescription", ""), "series")
        number = _safe_name(values.get("SeriesNumber", ""), "unknown")
        target_name = f"series-{number}_{description}"
        target_key = (study_ids[study_uid], category, target_name)
        if target_key in used_targets:
            suffix = hashlib.sha256(series_uid.encode()).hexdigest()[:8]
            target_name = f"{target_name}_{suffix}"
            target_key = (study_ids[study_uid], category, target_name)
        used_targets.add(target_key)
        result.append({
            "study": study_ids[study_uid],
            "category": category,
            "rule": rule_name,
            "status": status,
            "series_number": number,
            "description": values.get("SeriesDescription", ""),
            "protocol_name": values.get("ProtocolName", ""),
            "sequence_name": values.get("SequenceName", ""),
            "file_count": len(files),
            "target_name": target_name,
            "files": [file.path for file in files],
        })
    return result


def sort_dicom(source: Path, output: Path, rules: dict[str, Any]) -> list[dict[str, Any]]:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"DICOM input folder does not exist: {source}")
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Input and output must be separate folders")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output folder is not empty; refusing to overwrite: {output}")
    series = inspect_dicom(source, rules)
    if not series:
        raise ValueError("No DICOM series with Study and Series Instance UIDs were found")
    output.mkdir(parents=True, exist_ok=True)
    try:
        for item in series:
            target = output / item["study"] / item["category"] / item["target_name"]
            target.mkdir(parents=True, exist_ok=False)
            for path in item["files"]:
                destination = target / path.name
                if destination.exists():
                    destination = target / f"{path.stem}_{hashlib.sha256(str(path).encode()).hexdigest()[:8]}{path.suffix}"
                # Copy the DICOM payload only. Preserving macOS extended
                # attributes on filesystems such as exFAT creates AppleDouble
                # `._` sidecars for every instance and can nearly double the
                # visible file count in a sorted export.
                shutil.copyfile(path, destination)
            item["output"] = str(target)
            item.pop("files", None)
    except Exception:
        # Keep a failed run visibly incomplete; never remove or alter source data.
        raise
    return series
