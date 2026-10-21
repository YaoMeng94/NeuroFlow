"""Run dcm2bids against one verified DICOM study without copying source data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .bids_finalize import finalize_bids

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("converter")
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("participant")
    parser.add_argument("session")
    parser.add_argument("config")
    args = parser.parse_args(argv)
    source, output, config_path = (Path(p).expanduser().resolve() for p in
                                   (args.source, args.output, args.config))
    config = json.loads(config_path.read_text())
    study_id = config.pop("NeuroFlowStudyId", "")
    if not study_id or not study_id.startswith("study-"):
        parser.error("mapping config must include a verified NeuroFlowStudyId")
    manifest_path = Path(config.pop("NeuroFlowManifest", "")).resolve()
    uid_index = manifest_path.with_name("study_uid_index.json")
    index = json.loads(uid_index.read_text())
    if index.get("source_root") != str(source):
        parser.error("source directory does not match the inventory used to create this mapping")
    study = index.get("studies", {}).get(study_id)
    if not isinstance(study, dict) or not study.get("uid") or not study.get("files"):
        parser.error(f"study {study_id} is absent from the verified manifest")

    with tempfile.TemporaryDirectory(prefix=".neuroflow-dicom-", dir=source.parent) as temp:
        staged = Path(temp)
        count = 0
        for relative in study["files"]:
            path = (source / relative).resolve()
            if not path.is_relative_to(source) or not path.is_file():
                parser.error("study inventory references a missing or unsafe DICOM path")
            count += 1
            os.link(path, staged / f"{count:08d}.dcm")
        if not count:
            parser.error("no source DICOM instances matched the selected study")
        clean_config = staged / "dcm2bids_config.json"
        clean_config.write_text(json.dumps(config, indent=2) + "\n")
        cmd = [args.converter, "-d", str(staged), "-p", args.participant,
               "-c", str(clean_config), "-o", str(output)]
        if args.session:
            cmd[cmd.index("-o"):cmd.index("-o")] = ["-s", args.session]
        print(f"Selected {count} DICOM instances from {study_id}; starting dcm2bids", flush=True)
        subprocess.run(cmd, check=True)
    finalize_bids(output, args.participant, args.session, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
