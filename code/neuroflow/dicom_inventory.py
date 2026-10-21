"""Create dcm2bids helper output plus a conservative study-to-series index."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pydicom


def _clock(value: Any) -> str:
    return re.sub(r"[^0-9.]", "", str(value or ""))


def _study_label(date: str, time: str, index: int) -> str:
    date = re.sub(r"[^0-9]", "", date)
    time = _clock(time)
    if len(date) == 8:
        stamp = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        if len(time) >= 4:
            stamp += f" {time[:2]}:{time[2:4]}"
    else:
        stamp = "Date unavailable"
    return f"{stamp} (study {index:02d})"


def _study_stamp(date: Any, time: Any) -> str:
    date_digits = re.sub(r"[^0-9]", "", str(date or ""))
    match = re.match(r"^(\d{2})(\d{2})(\d{2})(?:\.(\d+))?", str(time or ""))
    if len(date_digits) != 8 or not match:
        return ""
    hour, minute, second, fraction = match.groups()
    total = Decimal(hour) * 3600 + Decimal(minute) * 60 + Decimal(second)
    if fraction:
        total += Decimal(f"0.{fraction}")
    rounded = int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    day = datetime.strptime(date_digits, "%Y%m%d") + timedelta(seconds=rounded)
    return day.strftime("%Y%m%d%H%M%S")


def _read_header(path: str) -> tuple[str, str, str, str] | None:
    try:
        ds = pydicom.dcmread(
            path, stop_before_pixels=True,
            specific_tags=["StudyInstanceUID", "StudyDate", "StudyTime", "SeriesInstanceUID"],
            force=False,
        )
    except Exception:
        return None
    return tuple(str(getattr(ds, key, "")) for key in
                 ("StudyInstanceUID", "StudyDate", "StudyTime", "SeriesInstanceUID"))


def _helper_progress(helper_dir: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    if not helper_dir.exists():
        return file_count, total_bytes
    for path in helper_dir.rglob("*"):
        try:
            if path.is_file():
                file_count += 1
                total_bytes += path.stat().st_size
        except OSError:
            continue
    return file_count, total_bytes


def run_helper(command: list[str], helper_dir: Path, progress_interval: float = 30.0) -> None:
    if progress_interval <= 0:
        raise ValueError("progress_interval must be positive")
    process = subprocess.Popen(command)
    started = time.monotonic()
    try:
        while True:
            try:
                returncode = process.wait(timeout=progress_interval)
                break
            except subprocess.TimeoutExpired:
                file_count, total_bytes = _helper_progress(helper_dir)
                elapsed = int(time.monotonic() - started)
                gibibytes = total_bytes / (1024 ** 3)
                print(
                    f"DICOM helper still running: {elapsed}s elapsed, "
                    f"{file_count} output files, {gibibytes:.2f} GiB written",
                    flush=True,
                )
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise


def build_manifest(source: Path, helper: Path, workers: int = 8) -> tuple[dict[str, Any], dict[str, Any]]:
    # Keep patient identifiers out of both indexes; Study UIDs stay in the private index.
    by_stamp: dict[str, set[str]] = defaultdict(set)
    study_meta: dict[str, tuple[str, str]] = {}
    study_files: dict[str, list[str]] = defaultdict(list)
    paths = [str(path) for path in source.rglob("*") if path.is_file()]
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        records = pool.map(_read_header, paths, chunksize=64)
        for path, record in zip(paths, records):
            if record is None:
                continue
            study_uid, date, time, series_uid = record
            if not study_uid or not series_uid:
                continue
            study_meta[study_uid] = (date, time)
            study_files[study_uid].append(str(Path(path).resolve().relative_to(source.resolve())))
            stamp = _study_stamp(date, time)
            if stamp:
                by_stamp[stamp].add(study_uid)

    studies = sorted(study_meta, key=lambda uid: (study_meta[uid], uid))
    group_ids = {uid: f"study-{i:02d}" for i, uid in enumerate(studies, 1)}
    groups = [{"id": group_ids[uid], "label": _study_label(*study_meta[uid], i)}
              for i, uid in enumerate(studies, 1)]
    sidecars: dict[str, str] = {}
    for path in sorted(helper.rglob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "SeriesNumber" not in data:
            continue
        timestamps = re.findall(r"20\d{12}", path.name)
        candidates = by_stamp[timestamps[-1]] if timestamps else set()
        sidecars[path.name] = group_ids[next(iter(candidates))] if len(candidates) == 1 else "unresolved"
    uid_index = {
        "source_root": str(source.resolve()),
        "studies": {
            group_ids[uid]: {"uid": uid, "files": study_files[uid]}
            for uid in studies
        },
    }
    return {"version": 1, "groups": groups, "sidecars": sidecars}, uid_index


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--index-existing", action="store_true")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("paths", nargs="+")
    opts = parser.parse_args(args)
    index_existing = opts.index_existing
    if len(opts.paths) != (2 if index_existing else 3) or opts.workers < 1:
        parser.error("expected SOURCE OUTPUT for --index-existing, otherwise HELPER SOURCE OUTPUT")
    helper_cmd, source_arg, output_arg = ("", *opts.paths) if index_existing else tuple(opts.paths)
    source, output = Path(source_arg).resolve(), Path(output_arg).resolve()
    if not index_existing:
        output.mkdir(parents=True, exist_ok=True)
        helper_dir = output / "tmp_dcm2bids" / "helper"
        run_helper([helper_cmd, "-d", str(source), "-o", str(output)], helper_dir)
    helper_dir = output / "tmp_dcm2bids" / "helper"
    manifest, study_uids = build_manifest(source, helper_dir, workers=opts.workers)
    target = output / "study_manifest.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n")
    target.chmod(0o600)
    uid_index = output / "study_uid_index.json"
    uid_index.write_text(json.dumps(study_uids, indent=2) + "\n")
    uid_index.chmod(0o600)
    print(f"Wrote study manifest: {target} ({len(manifest['groups'])} studies; "
          f"{sum(g == 'unresolved' for g in manifest['sidecars'].values())} unresolved sidecars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
