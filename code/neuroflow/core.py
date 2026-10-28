"""Pure job validation and command planning; never starts a process."""

from __future__ import annotations

import json
import base64
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any


STAGE_ORDER = {
    "anat": ("all", "preflight", "recon", "t2", "fslr"),
    "bold": ("all", "preflight", "fmriprep", "tedana", "postprocess", "cifti", "qc", "verify"),
    "dwi": ("all", "preflight", "qsiprep", "qsirecon", "metrics", "connectome", "verify"),
    "dicom": ("inventory", "convert"),
}
STAGES = {workflow: set(order) for workflow, order in STAGE_ORDER.items()}
LABEL = re.compile(r"^[A-Za-z0-9]+$")


class PlanError(ValueError):
    """A job cannot be planned without intervention."""


@dataclass(frozen=True)
class SiteProfile:
    pipeline_root: str
    freesurfer_home: str = ""
    fs_license_file: str = ""
    newmsm: str = ""
    atlas_root: str = ""
    wb_command: str = ""
    qc_python: str = "python3"
    ants_apply_transforms: str = ""
    fmriprep_image: str = ""
    tedana_image: str = ""
    tedana_command: str = ""
    qsiprep_image: str = ""
    qsirecon_image: str = ""
    qsiprep_merge_patch: str = ""
    apptainer: str = "apptainer"
    templateflow_home: str = ""
    dcm2bids: str = "dcm2bids"
    dcm2bids_helper: str = "dcm2bids_helper"
    dcm2niix: str = "dcm2niix"
    bids_validator: str = "bids-validator"
    bids_validator_image: str = ""
    nextflow: str = ""
    nextflow_java_home: str = ""
    atlas_datasets: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "SiteProfile":
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("site profile must be a JSON object")
            unknown = set(data) - set(cls.__dataclass_fields__)
            if unknown:
                raise ValueError(f"unknown site profile keys: {', '.join(sorted(unknown))}")
            if "atlas_datasets" in data and not isinstance(data["atlas_datasets"], dict):
                raise ValueError("atlas_datasets must be a name-to-path object")
            bundled_atlas_root = Path(__file__).resolve().parent / "data" / "atlases" / "standard_mesh_atlases"
            if not data.get("atlas_root") and bundled_atlas_root.is_dir():
                data["atlas_root"] = str(bundled_atlas_root)
            return cls(**data)
        except (OSError, TypeError, ValueError) as exc:
            raise PlanError(f"Cannot load site profile {path}: {exc}") from exc


@dataclass(frozen=True)
class JobSpec:
    workflow: str
    stage: str
    input_dir: str
    output_dir: str
    participant: str
    stages: tuple[str, ...] = ()
    session: str = ""
    nprocs: int = 16
    omp_nthreads: int = 8
    mem_mb: int = 32768
    atlases: tuple[str, ...] = ("Brainnetome246Ext", "Glasser360")
    bids_filter_file: str = ""
    fs_subjects_dir: str = ""
    anat_fslr_dir: str = ""
    anat_fs_subjects_dir: str = ""
    dicom_config: str = ""
    work_dir: str = ""
    skip_bids_validation: bool = False
    msm_threads: int = 4
    msm_config: str = ""
    tedana_fittype: str = "loglin"
    tedpca: str = "aic"
    ica_method: str = "fastica"
    comparison_echo: int = 0
    comparison_te_ms: float = 0.0
    fmriprep_cifti: bool = False
    output_spaces: str = ""
    fmriprep_dir: str = ""
    tedana_dir: str = ""
    mefmriprep_dir: str = ""
    gpu: str = "auto"
    eddy_config: str = ""
    processing_bids_dir: str = ""
    direct_bids_input: bool = False
    output_resolution: float = 1.5
    b0_policy: str = "auto"
    denoise_method: str = "dwidenoise"
    unringing_method: str = "mrdegibbs"
    distortion_group_merge: str = "concat"
    separate_all_dwis: bool = False
    dwi_b0_source: str = ""
    dwi_b0_sdc: str = "auto"
    geometry_policy: str = "auto"
    sdc_method: str = "auto"
    recon_spec: str = "auto"
    fd_threshold: float = 0.2
    smoothing_fwhm: float = 2.0
    execution_engine: str = "native"
    nextflow_resume: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "JobSpec":
        try:
            values = dict(data)
            stages = values.get("stages", ())
            if isinstance(stages, str):
                stages = tuple(item.strip() for item in stages.split(",") if item.strip())
            values["stages"] = tuple(stages)
            if values["stages"]:
                values["stage"] = values["stages"][0]
            if not values.get("participant") and values.get("workflow") == "dicom" and values.get("stage") == "inventory":
                values["participant"] = "inventory"
            for key in ("nprocs", "omp_nthreads", "mem_mb", "msm_threads", "comparison_echo"):
                raw = values.get(key)
                values[key] = int(cls.__dataclass_fields__[key].default if raw is None or raw == "" else raw)
            for key in ("fd_threshold", "smoothing_fwhm", "comparison_te_ms", "output_resolution"):
                raw = values.get(key)
                values[key] = float(cls.__dataclass_fields__[key].default if raw is None or raw == "" else raw)
            for key in ("skip_bids_validation", "fmriprep_cifti", "direct_bids_input", "separate_all_dwis", "nextflow_resume"):
                value = values.get(key, False)
                if isinstance(value, str):
                    if value.lower() not in {"true", "false", "on", "off", "1", "0", ""}:
                        raise ValueError(f"{key} must be a boolean")
                    value = value.lower() in {"true", "on", "1"}
                values[key] = bool(value)
            atlases = values.get("atlases", ("Brainnetome246Ext", "Glasser360"))
            if isinstance(atlases, str):
                atlases = tuple(x.strip() for x in atlases.split(",") if x.strip())
            values["atlases"] = tuple(atlases)
            values["participant"] = str(values["participant"]).removeprefix("sub-")
            values["session"] = str(values.get("session", "")).removeprefix("ses-")
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError(f"Invalid job specification: {exc}") from exc

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Plan:
    spec: JobSpec
    command: tuple[str, ...]
    warnings: tuple[str, ...]
    environment: dict[str, str] = field(default_factory=dict)
    post_command: tuple[str, ...] = ()
    sequence: tuple[tuple[str, ...], ...] = ()

    @property
    def commands(self) -> tuple[tuple[str, ...], ...]:
        return self.sequence or (self.command,)

    def as_dict(self) -> dict[str, Any]:
        return {"spec": self.spec.as_dict(), "command": list(self.command), "commands": [list(command) for command in self.commands], "warnings": list(self.warnings), "environment": self.environment, "post_command": list(self.post_command)}


def _directory(path: str, label: str) -> Path:
    result = Path(path).expanduser().resolve()
    if not result.is_dir():
        raise PlanError(f"{label} is not a directory: {result}")
    return result


def _file(path: str, label: str) -> Path:
    result = Path(path).expanduser().resolve()
    if not result.is_file():
        raise PlanError(f"{label} is not a file: {result}")
    return result


def _executable(value: str, label: str) -> str:
    found = shutil.which(value) if not Path(value).is_absolute() else value
    if not found or not Path(found).is_file():
        raise PlanError(f"{label} is unavailable: {value}")
    return str(Path(found).resolve())


def _configured_file(value: str, label: str) -> str:
    if not value:
        raise PlanError(f"Set {label} in the site profile")
    return str(_file(value, label))


def _validate_dcm2bids_config(path: str) -> Path:
    config = _file(path, "dcm2bids configuration")
    try:
        parsed = json.loads(config.read_text())
    except (OSError, ValueError) as exc:
        raise PlanError(f"Invalid dcm2bids JSON: {exc}") from exc
    descriptions = parsed.get("descriptions") if isinstance(parsed, dict) else None
    if not isinstance(descriptions, list) or not descriptions:
        raise PlanError("dcm2bids config must contain nonempty descriptions")
    for index, item in enumerate(descriptions, 1):
        if not isinstance(item, dict) or not all(item.get(k) for k in ("datatype", "suffix", "criteria")):
            raise PlanError(f"dcm2bids description {index} needs datatype, suffix, and criteria")
    if not parsed.get("NeuroFlowStudyId") or not parsed.get("NeuroFlowManifest"):
        raise PlanError("Use a study-specific mapping draft created from the verified DICOM inventory")
    return config


def plan_job(spec: JobSpec, site: SiteProfile, *, _validate_stage_dependencies: bool = True) -> Plan:
    selected = spec.stages or (spec.stage,)
    if spec.workflow not in STAGES or not selected or any(stage not in STAGES[spec.workflow] for stage in selected):
        raise PlanError(f"Unsupported workflow/stage selection: {spec.workflow}/{selected}")
    if len(set(selected)) != len(selected):
        raise PlanError("Select each stage only once")
    if "all" in selected and len(selected) > 1:
        raise PlanError("The all stage cannot be combined with individual stages")
    if (
        _validate_stage_dependencies
        and spec.workflow == "bold"
        and "cifti" in selected
        and "postprocess" not in selected
    ):
        output = Path(spec.output_dir).expanduser()
        work_dir = Path(spec.work_dir).expanduser() if spec.work_dir else output / ".work" / "mefmriprep"
        postprocess_dir = work_dir / "post_tedana"
        gsr_pattern = f"sub-{spec.participant}*_desc-tedanaGSR_bold.nii.gz"
        nogsr_pattern = f"sub-{spec.participant}*_desc-tedanaNoGSR_bold.nii.gz"
        gsr = list(postprocess_dir.rglob(gsr_pattern)) if postprocess_dir.is_dir() else []
        nogsr = list(postprocess_dir.rglob(nogsr_pattern)) if postprocess_dir.is_dir() else []
        if not gsr or not nogsr:
            raise PlanError(
                "BOLD CIFTI requires existing GSR and non-GSR post-tedana inputs in "
                f"{postprocess_dir}; select postprocess and CIFTI together, "
                "or use a work directory with compatible outputs"
            )
    if spec.workflow == "dicom" and len(selected) > 1:
        raise PlanError("DICOM inventory and conversion require separate reviewed jobs")
    if len(selected) > 1:
        ordered = tuple(stage for stage in STAGE_ORDER[spec.workflow] if stage in selected)
        plans = [plan_job(replace(spec, stage=stage, stages=()), site, _validate_stage_dependencies=False)
                 for stage in ordered]
        environment: dict[str, str] = {}
        for plan in plans:
            for key, value in plan.environment.items():
                if key in environment and environment[key] != value:
                    raise PlanError(f"Selected stages require conflicting {key} values")
                environment[key] = value
        warnings = list(dict.fromkeys(warning for plan in plans for warning in plan.warnings))
        positions = [STAGE_ORDER[spec.workflow].index(stage) for stage in ordered]
        includes_preflight = ordered[0] in {"fmriprep", "qsiprep"}
        if (positions[0] != 1 and not includes_preflight) or any(right - left > 1 for left, right in zip(positions, positions[1:])):
            warnings.append("Skipped stages must already have compatible outputs in the selected derivatives root.")
        ordered_spec = replace(spec, stage=ordered[0], stages=ordered)
        return Plan(ordered_spec, plans[0].command, tuple(warnings), environment, sequence=tuple(plan.command for plan in plans))
    if spec.stage != selected[0]:
        spec = replace(spec, stage=selected[0])
    is_dicom_inventory = spec.workflow == "dicom" and spec.stage == "inventory"
    if not is_dicom_inventory and (not LABEL.fullmatch(spec.participant) or (spec.session and not LABEL.fullmatch(spec.session))):
        raise PlanError("Subject/session labels must contain only letters and digits")
    if not 1 <= spec.nprocs <= 50 or not 1 <= spec.omp_nthreads <= 50 or (spec.workflow != "anat" and spec.omp_nthreads > spec.nprocs):
        raise PlanError("Require 1 <= OpenMP threads <= total processes <= 50")
    if spec.mem_mb < 1024:
        raise PlanError("Memory must be at least 1024 MiB")
    source = _directory(spec.input_dir, "Input")
    output = Path(spec.output_dir).expanduser().resolve()
    if not output.parent.is_dir():
        raise PlanError(f"Output parent directory does not exist: {output.parent}")
    if output.exists() and not output.is_dir():
        raise PlanError(f"Output path is not a directory: {output}")
    if not os.access(output.parent, os.W_OK | os.X_OK):
        raise PlanError(f"Output parent is not writable: {output.parent}")
    warnings: list[str] = []
    environment: dict[str, str] = {}
    post_command: tuple[str, ...] = ()

    if spec.workflow == "dicom":
        if spec.execution_engine != "native":
            raise PlanError("Nextflow execution is available for anatomy, BOLD, and DWI only")
        if output == source or output.is_relative_to(source):
            raise PlanError("DICOM output cannot be inside the raw input directory")
        converter = _executable(site.dcm2niix, "dcm2niix")
        environment["PATH"] = str(Path(converter).parent) + os.pathsep + os.environ.get("PATH", "")
        if spec.stage == "inventory":
            helper = _executable(site.dcm2bids_helper, "dcm2bids_helper")
            command = [sys.executable, "-m", "neuroflow.dicom_inventory",
                       "--workers", str(spec.nprocs),
                       helper, str(source), str(output)]
            if output.exists() and any(output.iterdir()):
                raise PlanError("Inventory output must be a new or empty directory")
        else:
            config = _validate_dcm2bids_config(spec.dicom_config)
            subject_dir = output / f"sub-{spec.participant}"
            target = subject_dir / f"ses-{spec.session}" if spec.session else subject_dir
            if target.exists():
                raise PlanError(f"Conversion target already exists; refusing overwrite: {target}")
            converter = _executable(site.dcm2bids, "dcm2bids")
            command = [sys.executable, "-m", "neuroflow.dicom_convert",
                       converter, str(source), str(output), spec.participant,
                       spec.session, str(config)]
            if site.bids_validator_image:
                validator = _configured_file(site.bids_validator_image, "bids_validator_image")
                apptainer = _executable(site.apptainer, "Apptainer")
                post_command = (apptainer, "exec", "--cleanenv", "-B", f"{output}:/bids:ro", validator, "bids-validator", "/bids")
            else:
                post_command = (_executable(site.bids_validator, "BIDS validator"), str(output))
            warnings.append("Review PE/readout, b0 versus SBRef, echo grouping, and fieldmap links before running.")
        return Plan(spec, tuple(command), tuple(warnings), environment, post_command)

    if not (source / "dataset_description.json").is_file():
        raise PlanError("Input lacks dataset_description.json; supply a BIDS dataset or convert DICOM first")
    if not (source / f"sub-{spec.participant}").is_dir():
        raise PlanError(f"Subject sub-{spec.participant} is absent from the BIDS dataset")
    if spec.session and not (source / f"sub-{spec.participant}" / f"ses-{spec.session}").is_dir():
        raise PlanError(f"Session ses-{spec.session} is absent from the BIDS dataset")
    if spec.workflow == "anat" and spec.session:
        raise PlanError("Anatomy launcher does not yet support session-scoped output; refusing possible overwrite")
    if source == output:
        raise PlanError("Derivatives output cannot equal the raw BIDS root")

    root = _directory(site.pipeline_root, "Pipeline installation")
    script = root / spec.workflow / f"run_{spec.workflow}_bidsapp.sh"
    _file(str(script), "Pipeline launcher")
    command = ["bash", str(script), str(source), str(output), "participant", "--participant-label", spec.participant]
    if spec.session:
        command += ["--session-id", spec.session]
    command += ["--stage", spec.stage, "--nprocs", str(spec.nprocs)]
    if spec.workflow != "anat":
        command += ["--omp-nthreads", str(spec.omp_nthreads), "--mem-mb", str(spec.mem_mb)]
    if spec.work_dir:
        command += ["--work-dir", str(Path(spec.work_dir).expanduser().resolve())]
    if spec.skip_bids_validation and spec.workflow != "anat":
        command += ["--skip-bids-validation"]
    if site.apptainer != "apptainer":
        environment["PRISMA_APPTAINER"] = _executable(site.apptainer, "Apptainer")
    if spec.bids_filter_file:
        command += ["--bids-filter-file", str(_file(spec.bids_filter_file, "BIDS filter"))]
    fs_subjects = Path(spec.fs_subjects_dir).expanduser().resolve() if spec.fs_subjects_dir else output / "freesurfer" / "subjects"
    anat_fslr = Path(spec.anat_fslr_dir).expanduser().resolve() if spec.anat_fslr_dir else output / "anatprep"
    if spec.workflow == "anat":
        if spec.stage in {"all", "fslr"}:
            if not 1 <= spec.msm_threads <= 25 or spec.msm_threads * 2 > spec.nprocs:
                raise PlanError("MSM threads must be 1-25 and two hemispheres must fit within total processes")
            command += ["--msm-threads", str(spec.msm_threads)]
            if spec.msm_config:
                command += ["--msm-config", str(_file(spec.msm_config, "MSM configuration"))]
        if spec.stage != "preflight":
            if not site.freesurfer_home:
                raise PlanError("Set freesurfer_home in the site profile")
            command += ["--freesurfer-home", str(_directory(site.freesurfer_home, "FreeSurfer home"))]
        if spec.stage in {"all", "fslr"}:
            command += ["--newmsm", _executable(site.newmsm or "newmsm", "newMSM")]
            if not site.atlas_root:
                raise PlanError("Set atlas_root in the site profile")
            command += ["--atlas-root", str(_directory(site.atlas_root, "Atlas root"))]
            command += ["--wb-command", _executable(site.wb_command or "wb_command", "Workbench")]
            command += ["--qc-python", _executable(site.qc_python, "Anatomy QC Python")]
        if site.fs_license_file:
            command += ["--fs-license-file", _configured_file(site.fs_license_file, "FreeSurfer license")]
    elif spec.workflow == "bold":
        for name, path in (
            ("fmriprep-dir", spec.fmriprep_dir),
            ("tedana-dir", spec.tedana_dir),
            ("mefmriprep-dir", spec.mefmriprep_dir),
        ):
            if path:
                command += [f"--{name}", str(Path(path).expanduser().resolve())]
        if spec.tedana_fittype not in {"loglin", "curvefit"} or spec.ica_method not in {"fastica", "robustica"}:
            raise PlanError("Unsupported tedana fitting or ICA method")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", spec.tedpca):
            raise PlanError("Invalid tedana PCA method")
        if spec.comparison_echo < 0 or not 0 <= spec.comparison_te_ms <= 100:
            raise PlanError("Comparison echo or TE is outside supported range")
        command += ["--tedana-fittype", spec.tedana_fittype, "--tedpca", spec.tedpca, "--ica-method", spec.ica_method]
        if spec.comparison_echo:
            command += ["--comparison-echo", str(spec.comparison_echo)]
        if spec.comparison_te_ms:
            command += ["--comparison-te-ms", str(spec.comparison_te_ms)]
        if spec.fmriprep_cifti:
            command += ["--fmriprep-cifti"]
        if spec.output_spaces:
            spaces = spec.output_spaces.split()
            if any(not re.fullmatch(r"[A-Za-z0-9_:.-]+", space) for space in spaces):
                raise PlanError("Invalid fMRIPrep output space")
            if spec.stage in {"all", "fmriprep"} and not {"T1w", "MNI152NLin6Asym"}.issubset(spaces):
                raise PlanError("BOLD output spaces must include T1w and MNI152NLin6Asym for final CIFTI")
            command += ["--output-spaces", " ".join(spaces)]
        if spec.stage in {"all", "fmriprep"}:
            command += ["--fmriprep-image", _configured_file(site.fmriprep_image, "fmriprep_image")]
        if spec.stage in {"all", "tedana"}:
            if site.tedana_command:
                command += ["--tedana-command", _executable(site.tedana_command, "tedana_command")]
            else:
                command += ["--tedana-image", _configured_file(site.tedana_image, "tedana_image")]
        if spec.stage in {"all", "fmriprep"} and spec.fs_subjects_dir:
            command += ["--fs-subjects-dir", str(fs_subjects)]
        if spec.stage in {"all", "fmriprep", "tedana", "postprocess", "cifti", "qc", "verify"}:
            command += ["--anat-fslr-dir", str(anat_fslr)]
        if spec.anat_fs_subjects_dir:
            command += ["--anat-fs-subjects-dir", str(Path(spec.anat_fs_subjects_dir).expanduser().resolve())]
        if site.fs_license_file:
            command += ["--fs-license-file", _configured_file(site.fs_license_file, "FreeSurfer license")]
        if spec.stage in {"all", "cifti"}:
            if not site.atlas_root:
                raise PlanError("Set atlas_root in the site profile")
            command += ["--atlas-root", str(_directory(site.atlas_root, "Atlas root"))]
            command += ["--wb-command", _executable(site.wb_command or "wb_command", "Workbench")]
            command += ["--ants-apply", _executable(site.ants_apply_transforms or "antsApplyTransforms", "ANTs ApplyTransforms")]
        if not 0 <= spec.fd_threshold <= 5 or not 0 <= spec.smoothing_fwhm <= 20:
            raise PlanError("BOLD FD threshold and smoothing FWHM are outside supported ranges")
        command += ["--fd-threshold", str(spec.fd_threshold), "--smoothing-fwhm", str(spec.smoothing_fwhm)]
    else:
        for name, value, allowed in (
            ("b0-policy", spec.b0_policy, {"auto", "include", "exclude"}),
            ("denoise-method", spec.denoise_method, {"dwidenoise", "patch2self", "none"}),
            ("unringing-method", spec.unringing_method, {"mrdegibbs", "rpg", "none"}),
            ("distortion-group-merge", spec.distortion_group_merge, {"concat", "average", "none"}),
        ):
            if value not in allowed:
                raise PlanError(f"Unsupported {name}: {value}")
            command += [f"--{name}", value]
        if not 0.5 <= spec.output_resolution <= 5:
            raise PlanError("DWI output resolution must be between 0.5 and 5 mm")
        command += ["--output-resolution", str(spec.output_resolution)]
        if spec.eddy_config:
            command += ["--eddy-config", str(_file(spec.eddy_config, "Eddy configuration"))]
        if spec.processing_bids_dir:
            command += ["--processing-bids-dir", str(Path(spec.processing_bids_dir).expanduser().resolve())]
        if spec.direct_bids_input:
            if spec.processing_bids_dir:
                raise PlanError("Direct BIDS input cannot be combined with a processing BIDS copy directory")
            command += ["--direct-bids-input"]
        if spec.separate_all_dwis:
            command += ["--separate-all-dwis"]
        if spec.stage in {"all", "qsiprep"}:
            command += ["--qsiprep-image", _configured_file(site.qsiprep_image, "qsiprep_image")]
            if site.qsiprep_merge_patch:
                environment["PRISMA_QSIPREP_DWI_MERGE_PATCH"] = _configured_file(site.qsiprep_merge_patch, "qsiprep_merge_patch")
        if spec.stage in {"all", "qsirecon", "metrics", "connectome"}:
            command += ["--qsirecon-image", _configured_file(site.qsirecon_image, "qsirecon_image")]
        if spec.stage not in {"preflight", "verify"}:
            command += ["--fs-subjects-dir", str(fs_subjects)]
        if site.fs_license_file:
            command += ["--fs-license-file", _configured_file(site.fs_license_file, "FreeSurfer license")]
        if site.templateflow_home:
            command += ["--templateflow-home", str(_directory(site.templateflow_home, "TemplateFlow home"))]
        if spec.gpu not in {"auto", "none", "eddy", "diffprep", "drbuddi", "all"}:
            raise PlanError(f"Unsupported GPU mode: {spec.gpu}")
        command += ["--gpu", spec.gpu]
        for name, value, allowed in (
            ("dwi-b0-sdc", spec.dwi_b0_sdc, {"auto", "none"}),
            ("geometry-policy", spec.geometry_policy, {"auto", "preserve", "resample"}),
            ("sdc-method", spec.sdc_method, {"auto", "topup", "drbuddi", "topup+drbuddi"}),
        ):
            if value not in allowed:
                raise PlanError(f"Unsupported {name}: {value}")
            command += [f"--{name}", value]
        if spec.dwi_b0_source:
            command += ["--dwi-b0-source", spec.dwi_b0_source]
        if spec.recon_spec:
            command += ["--recon-spec", spec.recon_spec]
        if spec.atlases:
            if not all(LABEL.fullmatch(x) for x in spec.atlases):
                raise PlanError("Atlas names must be alphanumeric")
            command += ["--atlases", *spec.atlases]
            for atlas in spec.atlases:
                if atlas in site.atlas_datasets:
                    command += ["--atlas-dataset", f"{atlas}={_directory(site.atlas_datasets[atlas], atlas + ' atlas')}"]
        else:
            command += ["--no-atlases"]
    if spec.workflow in {"anat", "bold", "dwi"} and spec.execution_engine == "nextflow":
        if selected != ("all",):
            raise PlanError("Nextflow currently supports complete modality workflows only")
        if spec.workflow == "bold" and any((spec.fmriprep_dir, spec.tedana_dir, spec.mefmriprep_dir)):
            raise PlanError("Nextflow manages BOLD derivative output paths; remove per-dataset output overrides")
        expected_derivatives = {
            "anat": ("freesurfer", "anatprep"),
            "bold": ("fmriprep", "tedana", "mefmriprep"),
            "dwi": ("qsiprep", "qsirecon", "dwiprep"),
        }[spec.workflow]
        for derivative in expected_derivatives:
            existing = output / derivative
            if existing.is_dir() and not existing.is_symlink():
                try:
                    has_contents = next(existing.iterdir(), None) is not None
                except OSError as exc:
                    raise PlanError(f"Cannot inspect existing derivative directory: {existing}") from exc
                if has_contents:
                    raise PlanError(f"Nextflow pilot will not replace an existing derivative directory: {existing}")
            elif existing.exists() and not existing.is_symlink():
                raise PlanError(f"Nextflow pilot will not replace an existing derivative path: {existing}")
            if existing.is_symlink() and not spec.nextflow_resume:
                raise PlanError("Existing Nextflow derivative links require Resume to be selected")
        nextflow = _executable(site.nextflow, "Nextflow")
        environment["NXF_OFFLINE"] = "true"
        if site.nextflow_java_home:
            java_home = _directory(site.nextflow_java_home, "Nextflow Java home")
            environment["JAVA_HOME"] = str(java_home)
            environment["JAVA_CMD"] = str(java_home / "bin" / "java")
        workflow_file = root / "nextflow" / "main.nf"
        _file(str(workflow_file), "Nextflow workflow")
        bundle = base64.urlsafe_b64encode(json.dumps({
            "command": command,
            "environment": environment,
            "participant": spec.participant,
            "workflow": spec.workflow,
        }).encode()).decode()
        nf_root = output / ".neuroflow" / "nextflow"
        command = [
            nextflow, "-log", str(nf_root / "nextflow.log"), "run", str(workflow_file),
            "-work-dir", str(nf_root / "work"),
            "-c", str(workflow_file.parent / "nextflow.config"),
            "--launcher_bundle_b64", bundle,
            "--input_dir", str(source),
            "--output_dir", str(output),
            "--workflow", spec.workflow,
            "--expected_derivatives", ",".join(expected_derivatives),
            "--nprocs", str(spec.nprocs),
            "--mem_mb", str(spec.mem_mb),
            "--publish_mode", "symlink",
        ]
        if spec.nextflow_resume:
            command.append("-resume")
        warnings.extend((
            f"Nextflow runs the existing {spec.workflow} launcher as one tracked task; internal stages are not separately cached.",
            "Nextflow cache tracks BIDS files by path, size, and modification time; edits preserving all three attributes may not invalidate resume.",
            "Published derivatives are symlinks into output/.neuroflow/nextflow/work; retain that directory while using these results.",
            "The local executor runs on this HPC node, not a cluster scheduler; launcher thread settings apply, while memory requests are recorded but may not be enforced locally.",
        ))
    elif spec.execution_engine != "native":
        raise PlanError("Nextflow execution is available for anatomy, BOLD, and DWI only")
    return Plan(spec, tuple(command), tuple(warnings), environment)
