"""Build a reviewed, per-session dcm2bids mapping from inventory rows."""

from __future__ import annotations

import re
from typing import Any

from .core import PlanError


DATATYPES = {"anat", "func", "dwi", "fmap"}
ENTITY = re.compile(r"^[A-Za-z0-9_-]+$")
T1_SERIES = re.compile(r"(?:3dT1w|T1w|MPRAGE|MP-RAGE|TFL)", re.IGNORECASE)


def default_t1w_row(rows: list[dict[str, Any]]) -> int | None:
    """Return a unique Siemens NORM T1w candidate for the inventory form."""
    candidates = []
    for index, row in enumerate(rows):
        labels = " ".join(str(row.get(key, "")) for key in ("SeriesDescription", "ProtocolName"))
        if not T1_SERIES.search(labels):
            continue
        image_types = []
        for key in ("ImageType", "ImageTypeText"):
            value = row.get(key, [])
            image_types.extend(value if isinstance(value, list) else str(value).split(","))
        normalized_types = {str(token).strip().upper() for token in image_types}
        if normalized_types.intersection({"DERIVED", "RESAMPLED"}) or "SCOUT" in labels.upper():
            continue
        if "NORM" in normalized_types:
            candidates.append(index)
    return candidates[0] if len(candidates) == 1 else None


def build_mapping(
    rows: list[dict[str, Any]], fields: dict[str, str], *,
    study_id: str = "", manifest_path: str = "",
) -> dict[str, Any]:
    if study_id == "unresolved":
        raise PlanError("Unresolved DICOM series cannot be mapped safely; review the source inventory")
    descriptions = []
    ids = set()
    pending_links: list[tuple[str, list[str]]] = []
    for index, row in enumerate(rows):
        prefix = f"row_{index}_"
        datatype = fields.get(prefix + "datatype", "skip")
        if datatype == "skip":
            continue
        if datatype not in DATATYPES:
            raise PlanError(f"Invalid datatype on series {row.get('SeriesNumber')}")
        suffix = fields.get(prefix + "suffix", "").strip()
        entities = fields.get(prefix + "entities", "").strip()
        identifier = fields.get(prefix + "id", "").strip()
        targets = [x.strip() for x in fields.get(prefix + "intendedfor", "").split(",") if x.strip()]
        if not suffix or not ENTITY.fullmatch(suffix):
            raise PlanError(f"Series {row.get('SeriesNumber')} needs a valid BIDS suffix")
        if entities and not ENTITY.fullmatch(entities):
            raise PlanError(f"Invalid BIDS entities on series {row.get('SeriesNumber')}")
        if identifier and (not ENTITY.fullmatch(identifier) or identifier in ids):
            raise PlanError(f"Duplicate or invalid mapping ID: {identifier}")
        if targets and datatype != "fmap":
            raise PlanError("IntendedFor targets are only accepted for fieldmap rows")
        if targets and not identifier:
            raise PlanError("A fieldmap with IntendedFor targets needs an ID")
        if identifier:
            ids.add(identifier)
        criteria: dict[str, Any] = {"SeriesNumber": row["SeriesNumber"]}
        if row.get("AcquisitionTime"):
            criteria["AcquisitionTime"] = row["AcquisitionTime"]
        elif row.get("source_group") not in (None, "unresolved"):
            raise PlanError(
                f"Series {row.get('SeriesNumber')} lacks AcquisitionTime; cannot safely map it across study groups"
            )
        if "EchoNumber" in row:
            criteria["EchoNumber"] = row["EchoNumber"]
        description: dict[str, Any] = {"datatype": datatype, "suffix": suffix, "criteria": criteria}
        if entities:
            description["custom_entities"] = entities
        if identifier:
            description["id"] = identifier
        changes: dict[str, Any] = {}
        task = re.search(r"(?:^|_)task-([A-Za-z0-9]+)(?:_|$)", entities)
        if datatype == "func" and suffix in {"bold", "sbref"} and not task:
            raise PlanError(f"Functional series {row.get('SeriesNumber')} needs a task- entity")
        if task:
            changes["TaskName"] = task.group(1)
        if targets:
            changes["IntendedFor"] = targets
            pending_links.append((identifier, targets))
        if changes:
            description["sidecar_changes"] = changes
        descriptions.append(description)
    if not descriptions:
        raise PlanError("Map at least one series before saving a conversion config")
    for identifier, targets in pending_links:
        for target in targets:
            if target not in ids:
                raise PlanError(f"Fieldmap {identifier} refers to unknown mapping ID {target}")
    seen = set()
    for item in descriptions:
        key = tuple(sorted(item["criteria"].items()))
        if key in seen:
            raise PlanError("Several mapped files share SeriesNumber/AcquisitionTime/EchoNumber; edit the JSON criteria manually")
        seen.add(key)
    # dcm2bids resolves IntendedFor IDs against descriptions it has already
    # processed, so ensure every target acquisition precedes its fieldmap.
    descriptions.sort(key=lambda item: item["datatype"] == "fmap")
    result: dict[str, Any] = {"descriptions": descriptions}
    if study_id:
        result["NeuroFlowStudyId"] = study_id
        result["NeuroFlowManifest"] = manifest_path
    return result
