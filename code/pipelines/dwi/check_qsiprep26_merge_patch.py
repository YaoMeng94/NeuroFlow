"""Small regression check for QSIPrep 26 final merge of resampled 3D volumes."""

import os
import inspect
import tempfile

import nibabel as nb
import numpy as np

from qsiprep.interfaces.dwi_merge import MergeDWIs, create_provenance_dataframe, harmonize_b0s
from qsiprep.interfaces.reports import calculate_motion_summary


with tempfile.TemporaryDirectory() as directory:
    files = []
    for i, value in enumerate((10, 11, 20, 21)):
        path = os.path.join(directory, f"vol{i}.nii.gz")
        nb.Nifti1Image(np.full((2, 2, 2), value, dtype=np.float32), np.eye(4)).to_filename(path)
        files.append(path)
    bval = os.path.join(directory, "all.bval")
    np.savetxt(bval, [0, 1000, 0, 1000], fmt="%d")
    sources = ["runA", "runA", "runB", "runB"]
    kwargs = {"bids_sources": sources} if "bids_sources" in inspect.signature(harmonize_b0s).parameters else {}
    images, means, corrections = harmonize_b0s(files, [bval], 100, True, **kwargs)
    assert len(images) == len(means) == len(corrections) == 4
    assert np.allclose(means, [10, 10, 20, 20])
    assert np.allclose(corrections, [1.5, 1.5, 0.75, 0.75])
    assert np.allclose([img.get_fdata().mean() for img in images], [15, 16.5, 15, 15.75])
    provenance = create_provenance_dataframe(sources, images, means, corrections)
    assert len(provenance) == 4
    assert provenance.original_file.tolist() == sources
    bvec = os.path.join(directory, "all.bvec")
    np.savetxt(bvec, np.zeros((3, 4)))
    motion = os.path.join(directory, "motion.tsv")
    with open(motion, "w") as stream:
        stream.write(
            "framewise_displacement\ttrans_x\ttrans_y\ttrans_z\trot_x\trot_y\trot_z\n"
            "0.0\t0.0\t0.0\t0.0\t0.0\t0.0\t0.0\n"
            "0.1\t0.1\t0.1\t0.1\t0.1\t0.1\t0.1\n"
            "0.0\t0.0\t0.0\t0.0\t0.0\t0.0\t0.0\n"
            "0.2\t0.2\t0.2\t0.2\t0.2\t0.2\t0.2\n"
        )
    raw_dir = os.path.join(directory, "merge_and_denoise_wf", "raw_merge")
    gradients_dir = os.path.join(directory, "merge_and_denoise_wf", "merge_dwis")
    os.makedirs(raw_dir)
    os.makedirs(gradients_dir)
    raw_dwi = os.path.join(raw_dir, "raw.nii.gz")
    nb.Nifti1Image(np.zeros((2, 2, 2, 4), dtype=np.float32), np.eye(4)).to_filename(raw_dwi)
    np.savetxt(os.path.join(gradients_dir, "raw.bvec"), np.zeros((3, 4)))
    os.mkdir(os.path.join(directory, "result"))
    result = MergeDWIs(
        dwi_files=files,
        bids_dwi_files=sources,
        bval_files=[bval],
        bvec_files=[bvec],
        raw_concatenated_files=[raw_dwi],
        denoising_confounds=[motion],
    ).run(cwd=os.path.join(directory, "result"))
    assert nb.load(result.outputs.out_dwi).shape == (2, 2, 2, 4)
    assert nb.load(result.outputs.merged_b0_ref).shape == (2, 2, 2)
    assert result.outputs.merged_raw_dwi == raw_dwi
    assert os.path.isfile(result.outputs.merged_raw_bvec)
    import pandas as pd
    merged_confounds = pd.read_csv(result.outputs.merged_denoising_confounds, sep="\t")
    assert "framewise_displacement" in merged_confounds.columns
    assert len(merged_confounds) == 4
    motion_summary = calculate_motion_summary(result.outputs.merged_denoising_confounds)
    assert "mean_fd" in motion_summary

    # For a single BIDS acquisition, the pre-HMC raw-image merge has no
    # separately written bvec in merge_dwis; final raw QC must fall back to
    # that acquisition's original BIDS bvec.
    fallback_base = os.path.join(directory, "single_source_case")
    fallback_raw_dir = os.path.join(
        fallback_base, "merge_and_denoise_wf", "raw_merge"
    )
    os.makedirs(fallback_raw_dir)
    fallback_raw_dwi = os.path.join(fallback_raw_dir, "raw.nii.gz")
    nb.Nifti1Image(
        np.zeros((2, 2, 2, 4), dtype=np.float32), np.eye(4)
    ).to_filename(fallback_raw_dwi)
    bids_dir = os.path.join(directory, "bids", "sub-SCJ", "dwi")
    os.makedirs(bids_dir)
    source_dwi = os.path.join(bids_dir, "sub-SCJ_acq-single_dwi.nii.gz")
    source_bvec = source_dwi[:-7] + ".bvec"
    np.savetxt(source_bvec, np.zeros((3, 4)))
    fallback_result_dir = os.path.join(directory, "single_source_result")
    os.mkdir(fallback_result_dir)
    fallback_result = MergeDWIs(
        dwi_files=files,
        bids_dwi_files=[source_dwi] * 4,
        bval_files=[bval],
        bvec_files=[bvec],
        raw_concatenated_files=[fallback_raw_dwi],
        denoising_confounds=[motion],
    ).run(cwd=fallback_result_dir)
    assert fallback_result.outputs.merged_raw_bvec == source_bvec

    # The pre-HMC path still needs comma-separated CSV for QSIPrep's
    # _gather_confounds consumer.
    single_cwd = os.path.join(directory, "single")
    os.mkdir(single_cwd)
    single = MergeDWIs(
        dwi_files=[raw_dwi],
        bids_dwi_files=["/bids/sub-SCJ/dwi/sub-SCJ_acq-test_dwi.nii.gz"],
        bval_files=[bval],
        bvec_files=[bvec],
    ).run(cwd=single_cwd)
    assert single.outputs.merged_denoising_confounds.endswith("_confounds.csv")
    assert "original_file" in pd.read_csv(single.outputs.merged_denoising_confounds).columns
print("PASS: final 3D merge supports SeriesQC; pre-HMC merge retains CSV for concat")
