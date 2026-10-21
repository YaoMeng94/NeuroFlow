"""Check the QSIRecon overlay relocates DWI masks without changing QSIPrep."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

import nibabel as nib
import numpy as np


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = root / "qsiprep"
    bids = root / "bids"
    anat = source / "sub-X/anat"
    dwi_dir = source / "sub-X/dwi"
    raw_dwi_dir = bids / "sub-X/dwi"
    anat.mkdir(parents=True)
    dwi_dir.mkdir(parents=True)
    raw_dwi_dir.mkdir(parents=True)
    (source / "dataset_description.json").write_text('{"Name":"synthetic QSIPrep"}\n')
    affine = np.eye(4)
    dwi_files = []
    for acq, values, vectors in [
        ("dir98", [0, 1500, 3000], [[0, 1, 0], [0, 0, 1], [0, 0, 0]]),
        ("dir99", [0, 1500, 3000], [[0, 0, 1], [0, 1, 0], [0, 0, 0]]),
    ]:
        dwi = dwi_dir / f"sub-X_acq-{acq}_dir-AP_space-ACPC_desc-preproc_dwi.nii.gz"
        nib.save(nib.Nifti1Image(np.full((3, 4, 5, 3), int(acq[-2:]), dtype=np.float32), affine), dwi)
        stem = dwi.name.removesuffix(".nii.gz")
        dwi.with_name(stem + ".bval").write_text(" ".join(map(str, values)) + "\n")
        np.savetxt(dwi.with_name(stem + ".bvec"), np.asarray(vectors, dtype=float))
        (raw_dwi_dir / f"sub-X_acq-{acq}_dir-AP_dwi.json").write_text(json.dumps({
            "PhaseEncodingDirection": "j-", "TotalReadoutTime": 0.0959,
            "Sources": [f"sub-X_acq-{acq}_dwi.nii.gz"],
        }))
        prefix = stem.replace("_space-ACPC_desc-preproc_dwi", "")
        with dwi.with_name(prefix + "_desc-confounds_timeseries.tsv").open("w") as handle:
            handle.write("original_file\tbval\n")
            for value in values:
                handle.write(f"sub-X_acq-{acq}_dwi.nii.gz\t{value}\n")
        dwi_mask_name = dwi.name.replace("_desc-preproc_dwi.nii.gz", "_desc-brain_mask.nii.gz")
        nib.save(nib.Nifti1Image(np.ones((3, 4, 5), dtype=np.uint8), affine), anat / dwi_mask_name)
        dwi_files.append(dwi)
    t1_mask_name = "sub-X_space-ACPC_desc-brain_mask.nii.gz"
    t1_mask = anat / t1_mask_name
    nib.save(nib.Nifti1Image(np.ones((3, 4, 5), dtype=np.uint8), affine), t1_mask)
    destination = root / "qsirecon_input"
    script = Path(__file__).with_name("prepare_qsirecon_input.py")
    subprocess.run([sys.executable, str(script), str(source), str(destination),
                    "--bids-dir", str(bids)], check=True)
    assert all(dwi.exists() for dwi in dwi_files)
    assert t1_mask.exists()
    assert not list((destination / "sub-X/anat").glob("*acq-*_desc-brain_mask.nii.gz"))
    assert (destination / "sub-X/anat" / t1_mask_name).exists()
    combined = destination / "sub-X/dwi/sub-X_dir-AP_space-ACPC_desc-preproc_dwi.nii.gz"
    assert combined.exists()
    np.testing.assert_array_equal(nib.load(combined).get_fdata()[0, 0, 0], [98, 98, 98, 99, 99, 99])
    assert (destination / "sub-X/dwi/sub-X_dir-AP_space-ACPC_desc-brain_mask.nii.gz").exists()
    assert (destination / "sub-X/dwi/sub-X_dir-AP_desc-confounds_timeseries.tsv").read_text().count("sub-X_acq-") == 6
    np.testing.assert_array_equal(np.loadtxt(str(combined).removesuffix(".nii.gz") + ".bval"), [0, 1500, 3000, 0, 1500, 3000])
    selection = root / "selection.json"
    subprocess.run([
        sys.executable, str(Path(__file__).with_name("select_reconstruction.py")),
        str(destination), "--output-manifest", str(selection),
    ], check=True, capture_output=True, text=True)
    selected = json.loads(selection.read_text())["Inputs"]
    assert len(selected) == 1 and selected[0]["Volumes"] == 6
    assert selected[0]["Sources"] == ["sub-X_acq-dir98_dwi.nii.gz", "sub-X_acq-dir99_dwi.nii.gz"]
    report = json.loads(destination.with_name("qsirecon_input_manifest.json").read_text())
    assert report["Version"] == 3
    assert report["BIDSDir"] == str(bids.resolve())
    assert report["CombinedAcquisitions"][0]["Volumes"] == 6
    assert len(report["CombinedAcquisitions"][0]["Inputs"]) == 2
print("QSIRECON_INPUT_OVERLAY:PASS")
