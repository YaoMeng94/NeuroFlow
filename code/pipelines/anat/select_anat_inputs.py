#!/usr/bin/env python3
"""Resolve one T1w image and an optional T2w image for a BIDS participant/session."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bids_dir", type=Path)
    p.add_argument("--participant-label", required=True)
    p.add_argument("--session-id")
    p.add_argument("--bids-filter-file", type=Path)
    return p.parse_args()


def entity(path: Path, key: str) -> str | None:
    match = re.search(rf"(?:^|_){re.escape(key)}-([^_]+)", path.name)
    return match.group(1) if match else None


def select(paths: list[Path], filters: dict[str, object], label: str) -> Path:
    aliases = {"acquisition": "acq", "reconstruction": "rec"}
    for key, expected in filters.items():
        if key in {"datatype", "suffix", "extension"}:
            continue
        if expected in (None, "*"):
            continue
        values = expected if isinstance(expected, list) else [expected]
        paths = [p for p in paths if entity(p, aliases.get(key, key)) in {str(v) for v in values}]
    if len(paths) != 1:
        raise SystemExit(f"{label}: expected one image after filtering, found {len(paths)}: {paths}")
    return paths[0]


def main() -> int:
    cfg = parse_args()
    subject = cfg.participant_label.removeprefix("sub-")
    root = cfg.bids_dir / f"sub-{subject}"
    if cfg.session_id:
        root /= f"ses-{cfg.session_id.removeprefix('ses-')}"
    filters = json.loads(cfg.bids_filter_file.read_text()) if cfg.bids_filter_file else {}
    t2_candidates = sorted(root.glob("anat/*_T2w.nii.gz"))
    payload = {
        "subject": f"sub-{subject}",
        "t1w": str(select(sorted(root.glob("anat/*_T1w.nii.gz")), filters.get("t1w", {}), "T1w")),
        "t2w": str(select(t2_candidates, filters.get("t2w", {}), "T2w")) if t2_candidates else None,
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
