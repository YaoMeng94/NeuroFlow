#!/usr/bin/env python3
"""Synthetic regression test for the reusable DWI input contract."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import nibabel as nib
import numpy as np

from verify_dwi_outputs import check_dwi


SCRIPT = Path(__file__).with_name("prepare_dwi_input.py")
PREFLIGHT_SCRIPT = Path(__file__).with_name("preflight_dwi.py")


def save_image(path: Path, shape: tuple[int, ...], affine: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    nib.save(nib.Nifti1Image(data, affine), str(path))


def write_json(path: Path, **values: object) -> None:
    path.write_text(json.dumps(values, indent=2) + "\n")


def entity_dwi_dir(root: Path, subject: str, session: str | None = None) -> Path:
    return root / subject / session / "dwi" if session else root / subject / "dwi"


def entity_fmap_dir(root: Path, subject: str, session: str | None = None) -> Path:
    return root / subject / session / "fmap" if session else root / subject / "fmap"


def add_directional(
    root: Path, subject: str, name: str, affine: np.ndarray, session: str | None = None
) -> None:
    dwi = entity_dwi_dir(root, subject, session) / f"{name}_dwi.nii.gz"
    save_image(dwi, (3, 2, 2, 3), affine)
    write_json(dwi.with_name(dwi.name.replace(".nii.gz", ".json")), PhaseEncodingDirection="j-", TotalReadoutTime=0.1)
    dwi.with_name(dwi.name.replace(".nii.gz", ".bval")).write_text("0 1000 1000\n")
    dwi.with_name(dwi.name.replace(".nii.gz", ".bvec")).write_text("1 0 0\n0 1 0\n0 0 1\n")
    sbref = dwi.with_name(dwi.name.replace("_dwi.nii.gz", "_sbref.nii.gz"))
    save_image(sbref, (3, 2, 2), affine)
    write_json(sbref.with_name(sbref.name.replace(".nii.gz", ".json")), PhaseEncodingDirection="j-", TotalReadoutTime=0.1)


def add_b0(root: Path, subject: str, session: str | None = None) -> Path:
    dwi = entity_dwi_dir(root, subject, session) / f"{subject}_acq-dir98b0_dir-PA_dwi.nii.gz"
    affine = np.diag([2.1, 2.0, 2.0, 1.0])
    save_image(dwi, (4, 2, 2, 2), affine)
    write_json(dwi.with_name(dwi.name.replace(".nii.gz", ".json")), PhaseEncodingDirection="j", TotalReadoutTime=0.1)
    dwi.with_name(dwi.name.replace(".nii.gz", ".bval")).write_text("0 0\n")
    dwi.with_name(dwi.name.replace(".nii.gz", ".bvec")).write_text("0 0\n0 0\n0 0\n")
    sbref = dwi.with_name(dwi.name.replace("_dwi.nii.gz", "_sbref.nii.gz"))
    save_image(sbref, (4, 2, 2), affine)
    write_json(sbref.with_name(sbref.name.replace(".nii.gz", ".json")), PhaseEncodingDirection="j", TotalReadoutTime=0.1)
    return dwi


def add_epi_fieldmaps(root: Path, subject: str, session: str | None = None) -> None:
    for direction, pe in (("AP", "j-"), ("PA", "j")):
        epi = entity_fmap_dir(root, subject, session) / f"{subject}_dir-{direction}_epi.nii.gz"
        save_image(epi, (3, 2, 2), np.diag([2.0, 2.0, 2.0, 1.0]))
        write_json(epi.with_name(epi.name.replace(".nii.gz", ".json")), PhaseEncodingDirection=pe, TotalReadoutTime=0.1)


def add_targeted_fieldmap(
    root: Path,
    subject: str,
    name: str,
    pe: str,
    readout: float,
    intended_for: list[str],
) -> Path:
    epi = entity_fmap_dir(root, subject) / f"{name}_epi.nii.gz"
    save_image(epi, (3, 2, 2), np.diag([2.0, 2.0, 2.0, 1.0]))
    write_json(
        epi.with_name(epi.name.replace(".nii.gz", ".json")),
        PhaseEncodingDirection=pe,
        TotalReadoutTime=readout,
        IntendedFor=intended_for,
    )
    return epi


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="prisma-dwi-test-") as tmp:
        root = Path(tmp)
        source = root / "source"
        output = root / "processing"
        overlay = root / "provenance" / "overlay.json"
        (source / "sub-01").mkdir(parents=True)
        (source / "sub-02").mkdir(parents=True)
        (source / "dataset_description.json").write_text('{"Name":"synthetic"}\n')
        add_directional(
            source,
            "sub-01",
            "sub-01_acq-dir98_dir-AP_run-1",
            np.diag([2.0, 2.0, 2.0, 1.0]),
            session="ses-01",
        )
        b0 = add_b0(source, "sub-01", session="ses-01")
        b0.with_name(b0.name.replace(".nii.gz", ".bval")).unlink()
        b0.with_name(b0.name.replace(".nii.gz", ".bvec")).unlink()
        add_epi_fieldmaps(source, "sub-01", session="ses-01")
        add_directional(
            source,
            "sub-01",
            "sub-01_acq-dir99_dir-AP",
            np.diag([2.0, 2.0, 2.0, 1.0]),
            session="ses-02",
        )
        add_b0(source, "sub-01", session="ses-02")
        add_epi_fieldmaps(source, "sub-01", session="ses-02")
        add_directional(source, "sub-02", "sub-02_acq-dir98_dir-AP", np.diag([2.0, 2.0, 2.0, 1.0]))
        add_directional(source, "sub-03", "sub-03_acq-dir98_dir-AP", np.diag([2.0, 2.0, 2.0, 1.0]))
        sub03_dwi = "sub-03_acq-dir98_dir-AP_dwi.nii.gz"
        add_targeted_fieldmap(
            source, "sub-03", "sub-03_acq-dwi_dir-PA", "j", 0.1, [f"dwi/{sub03_dwi}"]
        )
        add_targeted_fieldmap(
            source, "sub-03", "sub-03_acq-bold_dir-AP", "j-", 0.05,
            ["func/sub-03_task-rest_bold.nii.gz"],
        )
        add_targeted_fieldmap(
            source, "sub-03", "sub-03_acq-bold_dir-PA", "j", 0.05,
            ["func/sub-03_task-rest_bold.nii.gz"],
        )
        source_b0_bytes = b0.read_bytes()
        decoy_func = source / "sub-01" / "ses-01" / "func" / "sub-01_task-rest_bold.nii.gz"
        decoy_func.parent.mkdir(parents=True, exist_ok=True)
        decoy_func.write_bytes(b"unrelated functional data")
        output.mkdir(parents=True)
        (output / "stale.txt").write_text("stale")

        preflight_manifest = root / "preflight.json"
        preflight = subprocess.run(
            [
                sys.executable,
                str(PREFLIGHT_SCRIPT),
                str(source),
                "--participant-label",
                "01",
                "--session-id",
                "01",
                "--output-manifest",
                str(preflight_manifest),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if preflight.returncode:
            raise AssertionError(preflight.stdout + preflight.stderr)
        preflight_data = json.loads(preflight_manifest.read_text())
        assert preflight_data["Participants"] == ["sub-01"]
        assert preflight_data["Sessions"] == ["ses-01"]
        assert len(preflight_data["DWI"]) == 2
        assert all(item["session"] == "ses-01" for item in preflight_data["DWI"])

        command = [
            sys.executable,
            str(SCRIPT),
            str(source),
            str(output),
            "--participant-label",
            "01",
            "--session-id",
            "01",
            "--overwrite",
            "--overlay-path",
            str(overlay),
            "--b0-policy",
            "auto",
            "--dwi-b0-sdc",
            "auto",
            "--geometry-policy",
            "auto",
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

        manifest = json.loads(overlay.read_text())
        assert manifest["Participants"] == ["sub-01"]
        assert manifest["Sessions"] == ["ses-01"]
        assert manifest["DWIReversePEB0Fieldmaps"] == [
            "sub-01/ses-01/fmap/sub-01_ses-01_acq-b0_dir-PA_epi.nii.gz"
        ]
        assert manifest["ResampledDWI"] == []
        assert manifest["ResampledDWIReversePEB0Fieldmaps"] == [
            "sub-01/ses-01/fmap/sub-01_ses-01_acq-b0_dir-PA_epi.nii.gz"
        ]
        assert b0.read_bytes() == source_b0_bytes
        assert not (output / "sub-01/ses-01/func/sub-01_task-rest_bold.nii.gz").exists()
        assert not (output / "stale.txt").exists()

        promoted = output / manifest["DWIReversePEB0Fieldmaps"][0]
        assert promoted.exists()
        assert nib.load(str(promoted)).shape == (3, 2, 2, 2)
        assert not promoted.with_name(promoted.name.replace(".nii.gz", ".bval")).exists()
        assert not promoted.with_name(promoted.name.replace(".nii.gz", ".bvec")).exists()
        metadata = json.loads(promoted.with_name(promoted.name.replace(".nii.gz", ".json")).read_text())
        assert metadata["IntendedFor"] == ["dwi/sub-01_acq-dir98_dir-AP_run-1_dwi.nii.gz"]

        assert not (output / "sub-01/dwi/sub-01_acq-dir98b0_dir-PA_dwi.nii.gz").exists()
        assert not (output / "sub-01/ses-01/dwi/sub-01_acq-dir98b0_dir-PA_dwi.nii.gz").exists()
        assert not (output / "sub-01/ses-01/dwi/sub-01_acq-dir98b0_dir-PA_sbref.nii.gz").exists()
        assert (output / "sub-01/ses-01/dwi/sub-01_acq-dir98_dir-AP_run-1_dwi.nii.gz").exists()
        assert (output / "sub-01/ses-02/dwi/sub-01_acq-dir99_dir-AP_dwi.nii.gz").exists()
        assert (output / "sub-01/ses-02/dwi/sub-01_acq-dir98b0_dir-PA_dwi.nii.gz").exists()
        assert not (output / "sub-01/ses-02/fmap/sub-01_ses-02_acq-b0_dir-PA_epi.nii.gz").exists()
        assert (output / "sub-02/dwi/sub-02_acq-dir98_dir-AP_dwi.nii.gz").exists()
        assert not (output / "sub-01/ses-01/fmap/sub-01_dir-AP_epi.nii.gz").exists()
        assert not (output / "sub-01/ses-01/fmap/sub-01_dir-PA_epi.nii.gz").exists()

        # A linked reverse-PE EPI fmap is a valid DWI TOPUP candidate even
        # without a standalone b0-only DWI, and BOLD-only maps stay unlinked.
        sub03_manifest = root / "sub03-preflight.json"
        sub03_preflight = subprocess.run(
            [
                sys.executable,
                str(PREFLIGHT_SCRIPT),
                str(source),
                "--participant-label",
                "03",
                "--output-manifest",
                str(sub03_manifest),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if sub03_preflight.returncode:
            raise AssertionError(sub03_preflight.stdout + sub03_preflight.stderr)
        sub03_data = json.loads(sub03_manifest.read_text())
        assert not sub03_data["Warnings"]
        sub03_dwi_item = next(item for item in sub03_data["DWI"] if not item["is_b0_only"])
        assert sub03_dwi_item["opposite_pe_epi_fieldmaps"] == [
            "sub-03/fmap/sub-03_acq-dwi_dir-PA_epi.nii.gz"
        ]

        sub03_output = root / "sub03-processing"
        sub03_overlay = root / "sub03-overlay.json"
        sub03_prepare = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(source),
                str(sub03_output),
                "--participant-label",
                "03",
                "--overwrite",
                "--overlay-path",
                str(sub03_overlay),
                "--b0-policy",
                "auto",
                "--dwi-b0-sdc",
                "auto",
                "--geometry-policy",
                "auto",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if sub03_prepare.returncode:
            raise AssertionError(sub03_prepare.stdout + sub03_prepare.stderr)
        fmap_dir = entity_fmap_dir(sub03_output, "sub-03")
        dwi_fmap = fmap_dir / "sub-03_acq-dwi_dir-PA_epi.json"
        assert json.loads(dwi_fmap.read_text())["IntendedFor"] == [
            f"dwi/{sub03_dwi}"
        ]
        for bold_fmap in (
            "sub-03_acq-bold_dir-AP_epi",
            "sub-03_acq-bold_dir-PA_epi",
        ):
            assert not (fmap_dir / f"{bold_fmap}.json").exists()
            assert not (fmap_dir / f"{bold_fmap}.nii.gz").exists()
        sub03_overlay_data = json.loads(sub03_overlay.read_text())
        assert len(sub03_overlay_data["ExcludedUnavailableTargetFieldmaps"]) == 2

        # QSIPrep 26 needs the bundled 3D-per-volume merge compatibility
        # patch. Verify a normal launcher dry-run binds it automatically.
        qsiprep_26_image = root / "qsiprep_26.0.0.sif"
        qsiprep_26_image.touch()
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        fake_nvidia_smi = fake_bin / "nvidia-smi"
        fake_nvidia_smi.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-L\" ]; then echo 'GPU 0: test CUDA device'; exit 0; fi\n"
            "exit 1\n"
        )
        fake_nvidia_smi.chmod(0o755)
        fake_apptainer = fake_bin / "apptainer"
        fake_apptainer.write_text("#!/bin/sh\nexit 0\n")
        fake_apptainer.chmod(0o755)
        launcher_env = os.environ.copy()
        launcher_env["PATH"] = f"{fake_bin}:{launcher_env.get('PATH', '')}"
        launcher_result = subprocess.run(
            [
                "bash",
                str(Path(__file__).with_name("run_dwi_bidsapp.sh")),
                str(source),
                str(root / "launcher-dry-run"),
                "participant",
                "--participant-label",
                "03",
                "--stage",
                "qsiprep",
                "--nprocs",
                "2",
                "--omp-nthreads",
                "2",
                "--mem-mb",
                "4096",
                "--qsiprep-image",
                str(qsiprep_26_image),
                "--gpu",
                "eddy",
                "--dry-run",
            ],
            text=True,
            capture_output=True,
            check=False,
            env=launcher_env,
        )
        if launcher_result.returncode:
            raise AssertionError(launcher_result.stdout + launcher_result.stderr)
        merge_patch = Path(__file__).with_name("qsiprep26_dwi_merge_patched.py").resolve()
        patch_bind = (
            f"-B {merge_patch}:/app/.pixi/envs/qsiprep/lib/python3.10/"
            "site-packages/qsiprep/interfaces/dwi_merge.py:ro"
        )
        assert patch_bind in launcher_result.stdout, launcher_result.stdout
        assert "Would set default CUDA Eddy num_threads=2" in launcher_result.stdout, launcher_result.stdout + launcher_result.stderr

        # The bundled CUDA config must honor the launcher's OpenMP thread
        # setting without changing the reusable template or custom configs.
        configured_eddy = root / "eddy_params_cuda_threads2.json"
        configure_eddy = Path(__file__).with_name("configure_eddy_config.py")
        configure_result = subprocess.run(
            [
                sys.executable,
                str(configure_eddy),
                str(Path(__file__).with_name("eddy_params_cuda.json")),
                str(configured_eddy),
                "--num-threads",
                "2",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if configure_result.returncode:
            raise AssertionError(configure_result.stdout + configure_result.stderr)
        assert json.loads(configured_eddy.read_text())["num_threads"] == 2
        assert json.loads(Path(__file__).with_name("eddy_params_cuda.json").read_text())["num_threads"] == 1

        # Exercise the derivative verifier's sidecar contract independently of
        # a container run: complete DWI sidecars pass, while a missing bvec is
        # reported as an invalid derivative rather than silently skipped.
        source_dwi = output / "sub-01/ses-01/dwi/sub-01_acq-dir98_dir-AP_run-1_dwi.nii.gz"
        preproc_dwi = source_dwi.with_name(source_dwi.name.replace("_dwi.nii.gz", "_desc-preproc_dwi.nii.gz"))
        shutil.copy2(source_dwi, preproc_dwi)
        for suffix in (".bval", ".bvec"):
            shutil.copy2(
                source_dwi.with_name(source_dwi.name.replace(".nii.gz", suffix)),
                preproc_dwi.with_name(preproc_dwi.name.replace(".nii.gz", suffix)),
            )
        errors: list[str] = []
        checks: list[str] = []
        check_dwi(preproc_dwi, errors, checks)
        assert not errors
        preproc_dwi.with_name(preproc_dwi.name.replace(".nii.gz", ".bvec")).unlink()
        errors = []
        check_dwi(preproc_dwi, errors, checks)
        assert any("missing diffusion sidecar .bvec" in error for error in errors)
    print("DWI_SYNTHETIC_REGRESSION:PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
