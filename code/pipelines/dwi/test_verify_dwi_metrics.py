#!/usr/bin/env python3
"""Synthetic coverage for per-acquisition DWI scalar verification."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import nibabel as nib
import numpy as np


SCRIPT = Path(__file__).with_name("verify_dwi_metrics.py")
AFFINE = np.diag([1.5, 1.5, 1.5, 1.0])


def save_map(directory: Path, prefix: str, model: str, param: str, value: float, desc: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_model-{model}_param-{param}"
    if desc:
        name += f"_desc-{desc}"
    path = directory / f"{name}_dwimap.nii.gz"
    data = np.full((3, 3, 3), value, dtype=np.float32)
    nib.save(nib.Nifti1Image(data, AFFINE), str(path))
    return path


class VerifyDwiMetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="verify-dwi-metrics-")
        self.root = Path(self.temp.name)
        self.qsiprep = self.root / "qsiprep"
        self.qsirecon = self.root / "qsirecon"
        self.report = self.root / "metrics.json"
        dwi_dir = self.qsiprep / "sub-05" / "dwi"
        dwi_dir.mkdir(parents=True)
        self.dti_prefixes = [
            "sub-05_acq-dir98_dir-AP_space-ACPC",
            "sub-05_acq-dir99_dir-AP_space-ACPC",
        ]
        for prefix in self.dti_prefixes:
            (dwi_dir / f"{prefix}_desc-preproc_dwi.nii.gz").touch()
            out_dir = self.qsirecon / "derivatives" / "dwiprep-tensor" / "sub-05" / "dwi"
            for param, value in (("fa", 0.5), ("md", 0.001), ("ad", 0.0015), ("rd", 0.0008)):
                save_map(out_dir, prefix, "dti", param, value)

        noddi_dir = self.qsirecon / "derivatives" / "qsirecon-AMICONODDI" / "sub-05" / "dwi"
        noddi_prefix = "sub-05_dir-AP_space-ACPC"
        for param, value in (
            ("icvf", 0.6), ("od", 0.5), ("isovf", 0.1), ("tf", 0.7),
            ("rmse", 0.02), ("nrmse", 0.1),
        ):
            save_map(noddi_dir, noddi_prefix, "noddi", param, value)
        save_map(noddi_dir, noddi_prefix, "noddi", "icvf", 0.6, "modulated")
        save_map(noddi_dir, noddi_prefix, "noddi", "od", 0.5, "modulated")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_verifier(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--qsirecon-dir", str(self.qsirecon),
                "--qsiprep-dir", str(self.qsiprep),
                "--participant-label", "05",
                "--output-json", str(self.report),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_accepts_metrics_for_each_preprocessed_acquisition(self) -> None:
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["DTIAcquisitions"], self.dti_prefixes)
        self.assertEqual(len([row for row in report["Records"] if row["Model"] == "dti"]), 8)
        self.assertEqual(len([row for row in report["Records"] if row["Model"] == "noddi"]), 8)

    def test_detects_a_missing_metric_for_one_acquisition(self) -> None:
        missing = (
            self.qsirecon / "derivatives" / "dwiprep-tensor" / "sub-05" / "dwi"
            / f"{self.dti_prefixes[1]}_model-dti_param-rd_dwimap.nii.gz"
        )
        missing.unlink()
        result = self.run_verifier()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Expected one DTI rd map for " + self.dti_prefixes[1], result.stderr)


if __name__ == "__main__":
    unittest.main()
