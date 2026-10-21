import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from neuroflow.cli import _default_user_paths, main


class DefaultUserPathTests(unittest.TestCase):
    def test_windows_uses_roaming_profile_and_local_job_state(self) -> None:
        site, state = _default_user_paths(
            "win32",
            Path("/home/researcher"),
            {"APPDATA": "/profiles/roaming", "LOCALAPPDATA": "/profiles/local"},
        )
        self.assertEqual(site, Path("/profiles/roaming/NeuroFlow/site.json"))
        self.assertEqual(state, Path("/profiles/local/NeuroFlow/state/jobs"))

    def test_macos_uses_application_support(self) -> None:
        home = Path("/Users/researcher")
        site, state = _default_user_paths("darwin", home, {})
        support = home / "Library" / "Application Support" / "NeuroFlow"
        self.assertEqual(site, support / "site.json")
        self.assertEqual(state, support / "state" / "jobs")

    def test_linux_respects_xdg_directories(self) -> None:
        site, state = _default_user_paths(
            "linux",
            Path("/home/researcher"),
            {"XDG_CONFIG_HOME": "/profiles/config", "XDG_STATE_HOME": "/profiles/state"},
        )
        self.assertEqual(site, Path("/profiles/config/neuroflow/site.json"))
        self.assertEqual(state, Path("/profiles/state/neuroflow/jobs"))


class DoctorCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pipeline = self.root / "pipelines"
        for workflow in ("anat", "bold", "dwi"):
            folder = self.pipeline / workflow
            folder.mkdir(parents=True)
            (folder / f"run_{workflow}_bidsapp.sh").write_text("#!/usr/bin/env bash\n")
        self.apptainer = self.root / "bin" / "apptainer"
        self.apptainer.parent.mkdir()
        self.apptainer.write_text("#!/bin/sh\nexit 0\n")
        self.apptainer.chmod(0o755)
        self.newmsm = self.root / "bin" / "newMSM"
        self.workbench = self.root / "bin" / "wb_command"
        self.ants = self.root / "bin" / "antsApplyTransforms"
        for executable in (self.newmsm, self.workbench, self.ants):
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
        self.fs_home = self.root / "freesurfer"
        self.atlas_root = self.root / "atlases"
        self.fs_home.mkdir()
        self.atlas_root.mkdir()
        self.images = {}
        for name in ("fmriprep", "tedana", "qsiprep", "qsirecon"):
            image = self.root / f"{name}.sif"
            image.write_bytes(b"test image")
            self.images[name] = image
        self.site_path = self.root / "site.json"

    def _write_site(self, *, include_images=True):
        site = {
            "pipeline_root": str(self.pipeline),
            "apptainer": str(self.apptainer),
            "freesurfer_home": str(self.fs_home),
            "newmsm": str(self.newmsm),
            "atlas_root": str(self.atlas_root),
            "wb_command": str(self.workbench),
            "ants_apply_transforms": str(self.ants),
        }
        if include_images:
            site.update({f"{name}_image": str(image) for name, image in self.images.items()})
        self.site_path.write_text(json.dumps(site))

    def test_doctor_checks_all_three_modality_planners(self):
        self._write_site()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--site", str(self.site_path), "doctor"])
        self.assertEqual(result, 0)
        for workflow in ("ANAT", "BOLD", "DWI"):
            self.assertIn(f"PASS {workflow}", output.getvalue())

    def test_doctor_passes_for_configured_dwi_site(self):
        self._write_site()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--site", str(self.site_path), "doctor", "--workflow", "dwi"])
        self.assertEqual(result, 0)
        self.assertIn("PASS DWI", output.getvalue())
        self.assertIn("No jobs were launched", output.getvalue())

    def test_doctor_reports_missing_dwi_images_actionably(self):
        self._write_site(include_images=False)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--site", str(self.site_path), "doctor", "--workflow", "dwi"])
        self.assertEqual(result, 1)
        self.assertIn("FAIL DWI", output.getvalue())
        self.assertIn("Set qsiprep_image in the site profile", output.getvalue())

    def test_doctor_requires_java_for_nextflow(self):
        self._write_site()
        nextflow_dir = self.pipeline / "nextflow"
        nextflow_dir.mkdir()
        (nextflow_dir / "main.nf").write_text("// test\n")
        (nextflow_dir / "nextflow.config").write_text("process.executor = 'local'\n")
        nextflow = self.root / "bin" / "nextflow"
        nextflow.write_text("#!/bin/sh\nexit 0\n")
        nextflow.chmod(0o755)
        site = json.loads(self.site_path.read_text())
        site["nextflow"] = str(nextflow)
        self.site_path.write_text(json.dumps(site))
        output = io.StringIO()
        with patch.dict(os.environ, {"PATH": str(self.root / "empty-bin")}):
            with contextlib.redirect_stdout(output):
                result = main([
                    "--site", str(self.site_path), "doctor", "--workflow", "dwi",
                    "--engine", "nextflow",
                ])
        self.assertEqual(result, 1)
        self.assertIn("Java is unavailable", output.getvalue())


if __name__ == "__main__":
    unittest.main()
