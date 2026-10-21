"""Exercise noncontiguous source selection and rotated gradient preservation."""
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
    source = root / 'input/sub-X/dwi/sub-X_dir-AP_space-ACPC_desc-preproc_dwi.nii.gz'
    source.parent.mkdir(parents=True)
    (root / 'input/dataset_description.json').write_text('{"Name":"test","GeneratedBy":[]}')
    data = np.arange(32, dtype=np.float32).reshape(2, 2, 2, 4)
    nib.save(nib.Nifti1Image(data, np.eye(4)), source)
    stem = str(source).removesuffix('.nii.gz')
    Path(stem + '.bval').write_text('0 1500 0 3000')
    vectors = np.array([[0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 0, 0]])
    np.savetxt(stem + '.bvec', vectors)
    Path(stem + '.json').write_text('{"Sources":["keep","drop"]}')
    mask = root / 'input/sub-X/anat' / source.name.replace(
        '_desc-preproc_dwi.nii.gz', '_desc-brain_mask.nii.gz'
    )
    mask.parent.mkdir(parents=True)
    nib.save(nib.Nifti1Image(np.ones((2, 2, 2)), np.eye(4)), str(mask))
    with source.with_name('sub-X_dir-AP_desc-confounds_timeseries.tsv').open('w') as handle:
        writer = csv.writer(handle, delimiter='\t')
        writer.writerow(['original_file', 'bval'])
        writer.writerows([['keep', 0], ['keep', 1500], ['drop', 0], ['keep', 3000]])
    command = [sys.executable, str(Path(__file__).with_name('subset_reconstruction_input.py')),
               '--source-dwi', str(source), '--keep-source', 'keep', '--output-dir', str(root / 'output')]
    subprocess.run(command, check=True)
    output = root / 'output/sub-X/dwi' / source.name
    np.testing.assert_array_equal(nib.load(output).get_fdata(), data[..., [0, 1, 3]])
    np.testing.assert_array_equal(np.loadtxt(str(output).removesuffix('.nii.gz') + '.bvec'), vectors[:, [0, 1, 3]])
    assert json.loads((root / 'output/reconstruction_subset.json').read_text())['SelectedVolumeCount'] == 3
    assert subprocess.run(command, capture_output=True).returncode != 0
print('RECONSTRUCTION_SUBSET:PASS')
