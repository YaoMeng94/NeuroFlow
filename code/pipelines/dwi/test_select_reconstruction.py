"""Check automatic reconstruction selection when QSIPrep omits a DWI JSON."""

import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import nibabel as nib
import numpy as np


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    dwi_dir = root / "qsiprep/sub-SCJ/dwi"
    dwi_dir.mkdir(parents=True)
    dwi = dwi_dir / "sub-SCJ_acq-ccbd_dir-PA_space-ACPC_desc-preproc_dwi.nii.gz"
    nib.save(nib.Nifti1Image(np.ones((2, 2, 2, 5), dtype=np.float32), np.eye(4)), dwi)
    dwi.with_name(dwi.name.replace(".nii.gz", ".bval")).write_text("0 1000 1000 2000 2000\n")
    dwi.with_name(dwi.name.replace(".nii.gz", ".bvec")).write_text(
        "0 1 0 1 0\n0 0 1 0 1\n0 0 0 0 0\n"
    )
    confounds = dwi.with_name("sub-SCJ_acq-ccbd_dir-PA_desc-confounds_timeseries.tsv")
    with confounds.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["original_file"], delimiter="\t")
        writer.writeheader()
        writer.writerows([{"original_file": "ccbd_run.nii.gz"}] * 5)
    manifest = root / "selection.json"
    script = Path(__file__).with_name("select_reconstruction.py")
    result = subprocess.run(
        [sys.executable, str(script), str(root / "qsiprep"), "--output-manifest", str(manifest)],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(manifest.read_text())
    record = report["Inputs"][0]
    assert result.stdout.strip() == "mrtrix_multishell_msmt_ACT-hsvs"
    assert record["Sources"] == ["ccbd_run.nii.gz"]
    assert record["MetadataSource"] == "confounds_tsv"
    print("RECONSTRUCTION_SELECTION_METADATA:PASS")
