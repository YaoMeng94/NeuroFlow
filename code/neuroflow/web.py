"""Private, per-user browser interface for the shared job planner."""

from __future__ import annotations

import base64
import json
import secrets
import shlex
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .core import JobSpec, PlanError, STAGE_ORDER, SiteProfile, plan_job
from .dicom_sort import inspect_dicom, load_rules, sort_dicom
from .jobs import JobStore
from .mapping import build_mapping, default_t1w_row


PACKAGE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE / "templates"))
SAFE_DICOM_FIELDS = (
    "SeriesNumber", "SeriesDescription", "ProtocolName", "AcquisitionTime",
    "AcquisitionDuration", "ImageType", "ImageTypeText", "NonlinearGradientCorrection",
    "EchoNumber", "EchoTime", "RepetitionTime",
    "PhaseEncodingDirection", "TotalReadoutTime", "MultibandAccelerationFactor",
    "SliceThickness", "AcquisitionMatrixPE", "NumberOfVolumes", "SliceTiming",
)


def _study_manifest(path: str) -> tuple[Path, dict[str, object]]:
    root = Path(path).expanduser().resolve()
    for candidate in (root, *root.parents[:4]):
        manifest_path = candidate / "study_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text())
            except (OSError, ValueError) as exc:
                raise PlanError(f"Invalid DICOM study manifest: {exc}") from exc
            if manifest.get("version") == 1:
                return manifest_path, manifest
    raise PlanError("Verified study manifest not found; rerun DICOM inventory before mapping")


def _inventory(path: str, selected_group: str = "") -> list[dict[str, object]]:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise PlanError(f"Inventory folder not found: {root}")
    _, manifest = _study_manifest(path)
    sidecar_groups = manifest.get("sidecars", {})
    group_labels = {item["id"]: item["label"] for item in manifest.get("groups", [])}
    rows = []
    for sidecar in sorted(root.rglob("*.json"))[:300]:
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "SeriesNumber" not in data:
            continue
        group = sidecar_groups.get(sidecar.name, "unresolved")
        if selected_group and group != selected_group:
            continue
        row: dict[str, object] = {
            "file": str(sidecar.relative_to(root)),
            "source_group": group,
            "source_group_label": group_labels.get(group, "Unresolved DICOM study"),
        }
        for key in SAFE_DICOM_FIELDS:
            if key in data and key != "SliceTiming":
                value = data[key]
                if isinstance(value, list):
                    value = ", ".join(map(str, value))
                elif isinstance(value, dict):
                    value = json.dumps(value, sort_keys=True)
                row[key] = str(value)[:160] if isinstance(value, str) else value
        rows.append(row)
    return rows


def _inventory_groups(path: str) -> list[tuple[str, str]]:
    _, manifest = _study_manifest(path)
    groups = [(item["id"], item["label"]) for item in manifest.get("groups", [])]
    if "unresolved" in manifest.get("sidecars", {}).values():
        groups.append(("unresolved", "Unresolved DICOM study (mapping disabled)"))
    return groups


def _display_commands(commands: tuple[tuple[str, ...], ...] | list[list[str]], spec: JobSpec) -> tuple[list[str], list[str]]:
    if spec.execution_engine != "nextflow":
        return [shlex.join(command) for command in commands], []
    command = list(commands[0])
    if "--launcher_bundle_b64" not in command:
        return [shlex.join(command)], []
    bundle_index = command.index("--launcher_bundle_b64") + 1
    bundle = json.loads(base64.urlsafe_b64decode(command[bundle_index].encode()))
    command[bundle_index] = "<embedded launcher settings>"
    task_command = list(bundle["command"])
    task_command[3] = "<Nextflow task output root>"
    if "--work-dir" in task_command:
        task_command[task_command.index("--work-dir") + 1] = "<Nextflow task work directory>"
    else:
        task_command.extend(["--work-dir", "<Nextflow task work directory>"])
    return [shlex.join(command)], [shlex.join(task_command)]


def make_app(site: SiteProfile, jobs: JobStore) -> FastAPI:
    app = FastAPI(title="NeuroFlow", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="static")
    csrf = secrets.token_urlsafe(32)

    def render(request: Request, name: str, **context: object) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"csrf": csrf, "site": site, **context})

    def check_csrf(value: object) -> None:
        if not isinstance(value, str) or not secrets.compare_digest(value, csrf):
            raise HTTPException(status_code=403, detail="Invalid form token")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return render(request, "home.html", jobs=jobs.list()[:8], stages=STAGE_ORDER)

    @app.get("/sort", response_class=HTMLResponse)
    def sort_page(request: Request, source: str = "", output: str = "", rules: str = "") -> HTMLResponse:
        return render(request, "sort.html", source=source, output=output, rules=rules,
                      rows=[], error="", copied=False, reviewed=False)

    @app.post("/sort/preview", response_class=HTMLResponse)
    async def sort_preview(request: Request) -> HTMLResponse:
        form = await request.form()
        check_csrf(form.get("csrf"))
        source, output, rule_path = (str(form.get(key, "")).strip() for key in ("source", "output", "rules"))
        rows, error = [], ""
        try:
            if not source or not output or not rule_path:
                raise PlanError("Choose input, output, and rule preset paths")
            rules = load_rules(Path(rule_path).expanduser())
            rows = [{key: value for key, value in row.items() if key != "files"}
                    for row in inspect_dicom(Path(source), rules)]
            if not rows:
                error = "No DICOM series with Study and Series Instance UIDs were found."
        except (OSError, ValueError) as exc:
            error = str(exc)
        return render(request, "sort.html", source=source, output=output, rules=rule_path,
                      rows=rows, error=error, copied=False, reviewed=False)

    @app.post("/sort/apply", response_class=HTMLResponse)
    async def sort_apply(request: Request) -> HTMLResponse:
        form = await request.form()
        check_csrf(form.get("csrf"))
        source, output, rule_path = (str(form.get(key, "")).strip() for key in ("source", "output", "rules"))
        rows, error, copied = [], "", False
        try:
            if not source or not output or not rule_path:
                raise PlanError("Choose input, output, and rule preset paths")
            rules = load_rules(Path(rule_path).expanduser())
            plan = inspect_dicom(Path(source), rules)
            if not plan:
                raise PlanError("No DICOM series with Study and Series Instance UIDs were found")
            if any(row["status"] != "matched" for row in plan) and form.get("reviewed") != "yes":
                raise PlanError("Review unmatched and ambiguous series, then confirm to copy them into the review folders")
            rows = [{key: value for key, value in row.items() if key != "files"}
                    for row in sort_dicom(Path(source), Path(output), rules)]
            copied = True
        except (OSError, ValueError, PlanError) as exc:
            error = str(exc)
            try:
                rules = load_rules(Path(rule_path).expanduser())
                rows = [{key: value for key, value in row.items() if key != "files"}
                        for row in inspect_dicom(Path(source), rules)]
            except (OSError, ValueError):
                pass
        return render(request, "sort.html", source=source, output=output, rules=rule_path,
                      rows=rows, error=error, copied=copied,
                      reviewed=form.get("reviewed") == "yes")

    @app.post("/edit", response_class=HTMLResponse)
    async def edit_settings(request: Request) -> HTMLResponse:
        form = await request.form()
        check_csrf(form.get("csrf"))
        try:
            spec = JobSpec.from_mapping(json.loads(str(form["spec"])))
        except (KeyError, ValueError, PlanError) as exc:
            raise HTTPException(status_code=400, detail="Cannot restore reviewed job settings") from exc
        values = spec.as_dict()
        values["stages"] = list(spec.stages or (spec.stage,))
        values["atlases"] = ",".join(spec.atlases)
        return render(request, "home.html", jobs=jobs.list()[:8], stages=STAGE_ORDER, values=values)

    @app.post("/plan", response_class=HTMLResponse)
    async def preview(request: Request) -> HTMLResponse:
        form = await request.form()
        check_csrf(form.get("csrf"))
        try:
            values = {key: str(value) for key, value in form.items() if key != "csrf"}
            values["stages"] = form.getlist("stages") or ([str(form["stage"])] if "stage" in form else [])
            if not values["stages"]:
                raise PlanError("Select at least one stage")
            spec = JobSpec.from_mapping(values)
            plan = plan_job(spec, site)
            commands, task_commands = _display_commands(plan.commands, plan.spec)
            return render(request, "preview.html", plan=plan, commands=commands, task_commands=task_commands, spec_json=json.dumps(plan.spec.as_dict()))
        except (ValueError, PlanError) as exc:
            values = dict(form)
            values["stages"] = form.getlist("stages")
            return TEMPLATES.TemplateResponse(
                request, "home.html",
                {"csrf": csrf, "site": site, "jobs": jobs.list()[:8], "stages": STAGE_ORDER, "error": str(exc), "values": values},
                status_code=400,
            )

    @app.post("/run")
    async def run(request: Request) -> RedirectResponse:
        form = await request.form()
        check_csrf(form.get("csrf"))
        try:
            spec = JobSpec.from_mapping(json.loads(str(form["spec"])))
            plan = plan_job(spec, site)
            job_id = jobs.launch(plan)
        except (KeyError, ValueError, PlanError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def job_list(request: Request) -> HTMLResponse:
        return render(request, "jobs.html", jobs=jobs.list())

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str) -> HTMLResponse:
        try:
            job = jobs.get(job_id)
            log = jobs.log(job_id)[-50000:]
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        commands = job["plan"].get("commands") or [job["plan"]["command"]]
        plan = JobSpec.from_mapping(job["plan"]["spec"])
        commands, task_commands = _display_commands(commands, plan)
        pipeline_log = jobs.pipeline_log(job_id)[-50000:]
        return render(request, "job.html", job=job, log=log, pipeline_log=pipeline_log,
                      commands=commands, task_commands=task_commands)

    @app.get("/inventory", response_class=HTMLResponse)
    def inventory(request: Request, path: str = "") -> HTMLResponse:
        rows = []
        error = ""
        groups: list[tuple[str, str]] = []
        selected_group = request.query_params.get("source_group", "")
        if path:
            try:
                groups = _inventory_groups(path)
                if selected_group not in {group for group, _ in groups}:
                    selected_group = groups[0][0] if groups else ""
                rows = _inventory(path, selected_group)
            except PlanError as exc:
                error = str(exc)
        return render(request, "inventory.html", path=path, rows=rows, groups=groups,
                      selected_group=selected_group, suggested_t1w=default_t1w_row(rows), error=error)

    @app.post("/inventory/mapping", response_class=HTMLResponse)
    async def save_mapping(request: Request) -> HTMLResponse:
        form = await request.form()
        check_csrf(form.get("csrf"))
        path = str(form.get("path", ""))
        selected_group = str(form.get("source_group", ""))
        try:
            rows = _inventory(path, selected_group)
            manifest_path, _ = _study_manifest(path)
            mapping = build_mapping(rows, {key: str(value) for key, value in form.items()},
                                    study_id=selected_group, manifest_path=str(manifest_path))
            folder = jobs.root / "mappings"
            folder.mkdir(mode=0o700, exist_ok=True)
            target = folder / f"mapping-{uuid.uuid4().hex}.json"
            target.write_text(json.dumps(mapping, indent=2) + "\n")
            target.chmod(0o600)
        except (OSError, ValueError, PlanError) as exc:
            return TEMPLATES.TemplateResponse(
                request, "inventory.html",
                {"csrf": csrf, "site": site, "path": path,
                 "rows": _inventory(path, selected_group),
                 "groups": _inventory_groups(path), "selected_group": selected_group,
                 "suggested_t1w": default_t1w_row(_inventory(path, selected_group)),
                 "error": str(exc)},
                status_code=400,
            )
        return render(request, "mapping.html", path=str(target), content=json.dumps(mapping, indent=2))

    return app
