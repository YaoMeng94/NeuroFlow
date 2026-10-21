"""CLI entry point for site initialization, planning, running, and serving."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Mapping

from .core import JobSpec, PlanError, SiteProfile, plan_job
from .dicom_sort import load_rules, sort_dicom, write_rule_template
from .installation import initialize_site_profile, refresh_site_profile
from .jobs import JobStore


def _default_user_paths(
    platform_name: str | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    platform_name = platform_name or sys.platform
    home = home or Path.home()
    env = os.environ if env is None else env
    if platform_name == "win32":
        config_root = Path(env.get("APPDATA", home / "AppData" / "Roaming")) / "NeuroFlow"
        state_root = Path(env.get("LOCALAPPDATA", home / "AppData" / "Local")) / "NeuroFlow" / "state"
    elif platform_name == "darwin":
        support = home / "Library" / "Application Support" / "NeuroFlow"
        config_root = support
        state_root = support / "state"
    else:
        config_root = Path(env.get("XDG_CONFIG_HOME", home / ".config")) / "neuroflow"
        state_root = Path(env.get("XDG_STATE_HOME", home / ".local" / "state")) / "neuroflow"
    return config_root / "site.json", state_root / "jobs"


DEFAULT_SITE, DEFAULT_STATE = _default_user_paths()
LEGACY_SITE = Path.home() / ".config" / "prisma-workbench" / "site.json"
LEGACY_STATE = Path.home() / ".local" / "state" / "prisma-workbench" / "jobs"


def _preferred_path(current: Path, legacy: Path) -> Path:
    if current.exists() or not legacy.exists():
        return current
    return legacy


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="neuroflow")
    root.add_argument("--site", type=Path, default=_preferred_path(DEFAULT_SITE, LEGACY_SITE))
    root.add_argument("--state-dir", type=Path, default=_preferred_path(DEFAULT_STATE, LEGACY_STATE))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init-site", help="write a site profile template")
    commands.add_parser("refresh-site", help="update an old bundled pipeline path after an upgrade")
    for name in ("plan", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("spec", type=Path, help="job specification JSON")
    serve = commands.add_parser("serve", help="serve a private browser UI")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("jobs", help="list persisted jobs")
    doctor = commands.add_parser("doctor", help="check site configuration for pipeline readiness")
    doctor.add_argument("--workflow", choices=("anat", "bold", "dwi", "all"), default="all")
    doctor.add_argument("--engine", choices=("native", "nextflow"), default="native")
    rules = commands.add_parser("init-sort-rules", help="write an editable DICOM sorting rule template")
    rules.add_argument("path", type=Path)
    sorter = commands.add_parser("sort-dicom", help="sort DICOM instances into per-series folders")
    sorter.add_argument("input", type=Path)
    sorter.add_argument("output", type=Path)
    sorter.add_argument("--rules", type=Path, required=True)
    sorter.add_argument("--execute", action="store_true", help="copy files after showing the plan")
    sorter.add_argument("--accept-review", action="store_true", help="allow unmatched/ambiguous series into review folders")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "init-site":
            print(initialize_site_profile(args.site))
            return 0
        if args.command == "refresh-site":
            changed = refresh_site_profile(args.site)
            print("Updated bundled pipeline path." if changed else "Kept the configured pipeline path.")
            return 0
        if args.command == "jobs":
            print(json.dumps(JobStore(args.state_dir).list(), indent=2))
            return 0
        if args.command == "doctor":
            site = SiteProfile.load(args.site)
            workflows = ("anat", "bold", "dwi") if args.workflow == "all" else (args.workflow,)
            failures = []
            with tempfile.TemporaryDirectory(prefix="neuroflow-doctor-") as temporary:
                root = Path(temporary)
                bids = root / "bids"
                (bids / "sub-01").mkdir(parents=True)
                (root / "derivatives").mkdir()
                (bids / "dataset_description.json").write_text(
                    '{"Name":"NeuroFlow environment check","BIDSVersion":"1.9.0"}\n'
                )
                for workflow in workflows:
                    spec = JobSpec(
                        workflow=workflow, stage="all", input_dir=str(bids),
                        output_dir=str(root / "derivatives" / workflow), participant="01",
                        execution_engine=args.engine,
                    )
                    try:
                        plan_job(spec, site)
                        if workflow in {"bold", "dwi"}:
                            apptainer = shutil.which(site.apptainer)
                            if not apptainer:
                                raise PlanError(
                                    f"Apptainer is unavailable: {site.apptainer}. "
                                    "Install Apptainer or set its executable path in the site profile."
                                )
                        if args.engine == "nextflow":
                            java = (
                                Path(site.nextflow_java_home).expanduser() / "bin" / "java"
                                if site.nextflow_java_home else Path(shutil.which("java") or "")
                            )
                            if not java.is_file() or not os.access(java, os.X_OK):
                                raise PlanError(
                                    "Java is unavailable. Install a supported Java runtime or set "
                                    "nextflow_java_home in the site profile."
                                )
                    except (OSError, PlanError, RuntimeError) as exc:
                        failures.append((workflow, str(exc)))
                        print(f"FAIL {workflow.upper()}: {exc}")
                    else:
                        print(f"PASS {workflow.upper()}: required site paths satisfy planner checks")
            print("No jobs were launched; container images were not executed or version-checked.")
            return 1 if failures else 0
        if args.command == "init-sort-rules":
            write_rule_template(args.path.expanduser())
            print(args.path.expanduser())
            return 0
        if args.command == "sort-dicom":
            rules = load_rules(args.rules.expanduser())
            from .dicom_sort import inspect_dicom

            plan = inspect_dicom(args.input, rules)
            if not plan:
                raise PlanError("No DICOM series with Study and Series Instance UIDs were found")
            print(json.dumps([{key: value for key, value in row.items() if key != "files"} for row in plan], indent=2))
            if not args.execute:
                print("\nDry run only. Add --execute to copy these series.")
                return 0
            needs_review = any(row["status"] != "matched" for row in plan)
            if needs_review and not args.accept_review:
                raise PlanError("Unmatched/ambiguous series need review; rerun with --accept-review to place them in review folders")
            result = sort_dicom(args.input, args.output, rules)
            print(json.dumps([{key: value for key, value in row.items() if key != "files"} for row in result], indent=2))
            return 0
        site = SiteProfile.load(args.site)
        if args.command == "serve":
            import uvicorn

            from .web import make_app

            uvicorn.run(make_app(site, JobStore(args.state_dir)), host="127.0.0.1", port=args.port)
            return 0
        spec = JobSpec.from_mapping(json.loads(args.spec.read_text()))
        plan = plan_job(spec, site)
        if args.command == "plan":
            print(json.dumps(plan.as_dict(), indent=2))
        else:
            print(JobStore(args.state_dir).launch(plan))
        return 0
    except (OSError, ValueError, PlanError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
