import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys

from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from neuroflow.dicom_inventory import build_manifest, run_helper


def write_instance(path: Path, study_uid: str, series_uid: str, study_time: str) -> None:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = generate_uid()
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.StudyDate = "20261012"
    ds.StudyTime = study_time
    ds.SeriesNumber = 10
    ds.EchoNumbers = 1
    ds.PatientID = "must-not-be-written"
    ds.save_as(path, enforce_file_format=True)


class DicomInventoryTests(unittest.TestCase):
    def test_helper_emits_progress_while_running(self):
        with tempfile.TemporaryDirectory() as temp:
            helper_dir = Path(temp) / "helper"
            helper_dir.mkdir()
            (helper_dir / "partial.nii.gz").write_bytes(b"data")
            output = io.StringIO()
            command = [sys.executable, "-c", "import time; time.sleep(0.06)"]
            with redirect_stdout(output):
                run_helper(command, helper_dir, progress_interval=0.01)
        self.assertIn("DICOM helper still running", output.getvalue())
        self.assertIn("1 output files", output.getvalue())

    def test_helper_nonzero_exit_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, "-c", "raise SystemExit(7)"]
            with self.assertRaises(subprocess.CalledProcessError) as error:
                run_helper(command, Path(temp) / "helper", progress_interval=0.1)
        self.assertEqual(error.exception.returncode, 7)

    def test_filename_timestamp_must_match_dicom_study_datetime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, helper = root / "source", root / "helper"
            source.mkdir()
            helper.mkdir()
            studies = (("1.2.3", "1.2.3.1", "090000.0", "20261012090000"),
                       ("1.2.4", "1.2.4.1", "103000.0", "20261012103000"))
            for index, (study, series, study_time, stamp) in enumerate(studies):
                write_instance(source / f"instance-{index}.dcm", study, series, study_time)
                (helper / f"sidecar-{index}_{stamp}.json").write_text(json.dumps({
                    "SeriesNumber": 10, "AcquisitionTime": "12:00:00", "EchoNumber": 1,
                }))
            manifest, uid_index = build_manifest(source, helper)
            self.assertEqual(len(manifest["groups"]), 2)
            self.assertEqual(set(manifest["sidecars"].values()), {"study-01", "study-02"})
            serialized = json.dumps(manifest)
            self.assertNotIn("must-not-be-written", serialized)
            self.assertNotIn("1.2.3", serialized)
            self.assertEqual(uid_index["studies"]["study-01"]["uid"], "1.2.3")
            self.assertEqual(len(uid_index["studies"]["study-01"]["files"]), 1)

    def test_ambiguous_sidecar_is_unresolved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, helper = root / "source", root / "helper"
            source.mkdir()
            helper.mkdir()
            for index, study in enumerate(("1.2.3", "1.2.4")):
                write_instance(source / f"instance-{index}.dcm", study, f"{study}.1", "090000")
            (helper / "series_20261012090000.json").write_text(json.dumps({
                "SeriesNumber": 10, "AcquisitionTime": "090000", "EchoNumber": 1,
            }))
            manifest, _ = build_manifest(source, helper)
            self.assertEqual(manifest["sidecars"]["series_20261012090000.json"], "unresolved")


if __name__ == "__main__":
    unittest.main()
