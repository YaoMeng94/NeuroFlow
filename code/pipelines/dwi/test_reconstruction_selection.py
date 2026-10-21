"""Regression tests for shell-aware reconstruction selection."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import nibabel as nib
import numpy as np

from select_reconstruction import resolve_spec, shell_counts


class ReconstructionSelectionTest(unittest.TestCase):
    def test_shells_and_presets(self):
        self.assertEqual([r['volumes'] for r in shell_counts([0, 0, 1498, 1502, 3000])], [2, 2, 1])
        self.assertEqual(resolve_spec('auto', [2]), 'mrtrix_multishell_msmt_ACT-hsvs')
        self.assertEqual(resolve_spec('auto', [1]), 'mrtrix_singleshell_ss3t_ACT-hsvs')
        for spec, counts in [('auto', []), ('auto', [0]), ('auto', [1, 2]),
                             ('mrtrix_singleshell_ss3t_ACT-hsvs', [2]),
                             ('mrtrix_multishell_msmt_ACT-hsvs', [1])]:
            with self.subTest(spec=spec, counts=counts), self.assertRaises(ValueError):
                resolve_spec(spec, counts)
        for values in [[], [-1], [float('nan')]]:
            with self.assertRaises(ValueError):
                shell_counts(values)

    def test_manifest_filtering_and_gradient_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for subject, values in [('A', [0, 1500, 3000]), ('B', [0, 1000, 1000])]:
                stem = root / f'sub-{subject}' / 'ses-1' / 'dwi' / f'sub-{subject}_ses-1_space-ACPC_desc-preproc_dwi'
                stem.parent.mkdir(parents=True)
                nib.save(nib.Nifti1Image(np.zeros((2, 2, 2, 3)), np.eye(4)), str(stem) + '.nii.gz')
                Path(str(stem) + '.bval').write_text(' '.join(map(str, values)))
                Path(str(stem) + '.bvec').write_text('0 1 0\n0 0 1\n0 0 0\n')
                Path(str(stem) + '.json').write_text(json.dumps({'Sources': ['dir98', 'dir99']}))
            manifest = root / 'selection.json'
            command = [sys.executable, str(Path(__file__).with_name('select_reconstruction.py')),
                       str(root), '--output-manifest', str(manifest)]
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            selected = command + ['--participant-label', 'A', '--session-id', '1']
            result = subprocess.run(selected, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(manifest.read_text())
            self.assertEqual(report['Inputs'][0]['Volumes'], 3)
            self.assertEqual(report['Inputs'][0]['Sources'], ['dir98', 'dir99'])
            self.assertEqual(len(report['Inputs']), 1)
            self.assertIn('multishell_msmt', result.stdout)
            bval = next(root.glob('sub-A/**/*.bval'))
            bval.write_text('0 1500')
            self.assertNotEqual(subprocess.run(selected, capture_output=True).returncode, 0)
            bval.write_text('0 1500 3000')
            bval.with_suffix('.bvec').write_text('0 1\n0 1\n0 1\n')
            self.assertNotEqual(subprocess.run(selected, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
