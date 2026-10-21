import json
import tempfile
import unittest
from pathlib import Path

from neuroflow.bids_finalize import finalize_bids


class BidsFinalizeTests(unittest.TestCase):
    def test_writes_dataset_metadata_resolves_intendedfor_and_removes_fmap_gradients(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subject = root / "sub-01"
            fmap = subject / "fmap"
            func = subject / "func"
            fmap.mkdir(parents=True)
            func.mkdir()
            (func / "sub-01_task-rest_run-1_echo-1_bold.json").write_text("{}\n")
            (func / "sub-01_task-rest_run-1_echo-1_bold.nii.gz").touch()
            fmap_json = fmap / "sub-01_dir-PA_epi.json"
            fmap_json.write_text(json.dumps({"PhaseEncodingDirection": "j-"}))
            (fmap / "sub-01_dir-PA_epi.nii.gz").touch()
            (fmap / "sub-01_dir-PA_epi.bval").write_text("0\n")
            (fmap / "sub-01_dir-PA_epi.bvec").write_text("0\n0\n0\n")
            dwi = subject / "dwi"
            dwi.mkdir()
            (dwi / "sub-01_acq-dir98_dir-AP_sbref.nii.gz").touch()
            (dwi / "sub-01_acq-dir98_dir-AP_sbref.bval").write_text("0\n")
            (dwi / "sub-01_acq-dir98_dir-AP_sbref.bvec").write_text("0\n0\n0\n")
            config = {"descriptions": [
                {"id": "bold1", "datatype": "func", "suffix": "bold",
                 "custom_entities": "task-rest_run-1_echo-1"},
                {"id": "fieldmap", "datatype": "fmap", "suffix": "epi",
                 "custom_entities": "dir-PA", "sidecar_changes": {"IntendedFor": ["bold1"]}},
            ]}

            finalize_bids(root, "01", "", config)

            description = json.loads((root / "dataset_description.json").read_text())
            self.assertEqual(description["BIDSVersion"], "1.9.0")
            self.assertEqual(json.loads(fmap_json.read_text())["IntendedFor"], [
                "func/sub-01_task-rest_run-1_echo-1_bold.nii.gz"
            ])
            self.assertFalse((fmap / "sub-01_dir-PA_epi.bval").exists())
            self.assertFalse((fmap / "sub-01_dir-PA_epi.bvec").exists())
            self.assertFalse((dwi / "sub-01_acq-dir98_dir-AP_sbref.bval").exists())
            self.assertFalse((dwi / "sub-01_acq-dir98_dir-AP_sbref.bvec").exists())

    def test_intendedfor_paths_include_session_entity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subject = root / "sub-02" / "ses-01"
            (subject / "fmap").mkdir(parents=True)
            (subject / "func").mkdir()
            (subject / "func" / "sub-02_ses-01_task-rest_bold.json").write_text("{}\n")
            (subject / "func" / "sub-02_ses-01_task-rest_bold.nii.gz").touch()
            fmap_json = subject / "fmap" / "sub-02_ses-01_dir-PA_epi.json"
            fmap_json.write_text("{}\n")
            (subject / "fmap" / "sub-02_ses-01_dir-PA_epi.nii.gz").touch()
            config = {"descriptions": [
                {"id": "bold", "datatype": "func", "suffix": "bold", "custom_entities": "task-rest"},
                {"id": "fmap", "datatype": "fmap", "suffix": "epi", "custom_entities": "dir-PA",
                 "sidecar_changes": {"IntendedFor": ["bold"]}},
            ]}

            finalize_bids(root, "02", "01", config)

            self.assertEqual(json.loads(fmap_json.read_text())["IntendedFor"], [
                "ses-01/func/sub-02_ses-01_task-rest_bold.nii.gz"
            ])

    def test_archives_intermediates_without_colliding_with_sibling_conversion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bids"
            subject = root / "sub-03" / "anat"
            subject.mkdir(parents=True)
            (subject / "sub-03_T1w.json").write_text("{}\n")
            (subject / "sub-03_T1w.nii.gz").touch()
            temporary_root = root / "tmp_dcm2bids"
            (temporary_root / "sub-03").mkdir(parents=True)
            (temporary_root / "log").mkdir()
            (temporary_root / "sub-03" / "intermediate.nii.gz").write_text("intermediate")
            (temporary_root / "log" / "sub-03_run.log").write_text("conversion log")
            (temporary_root / "log" / "bids_schema_stable.json").write_text("{}")
            (temporary_root / "log" / "version_check.json").write_text("{}")
            existing_archive = root.parent / "dcm2bids_intermediates" / "sub-03"
            existing_archive.mkdir(parents=True)
            (existing_archive / "preserve.txt").write_text("prior conversion")

            finalize_bids(root, "03", "", {"descriptions": []})

            archive = root.parent / "dcm2bids_intermediates" / root.name
            self.assertFalse(temporary_root.exists())
            self.assertEqual((archive / "sub-03" / "intermediate.nii.gz").read_text(), "intermediate")
            self.assertEqual((archive / "log" / "sub-03_run.log").read_text(), "conversion log")
            self.assertTrue((archive / "log" / "bids_schema_stable.json").is_file())
            self.assertTrue((archive / "log" / "version_check.json").is_file())
            self.assertEqual((existing_archive / "preserve.txt").read_text(), "prior conversion")


if __name__ == "__main__":
    unittest.main()
