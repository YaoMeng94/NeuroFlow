from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydicom import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

from neuroflow.dicom_sort import inspect_dicom, sort_dicom


def write_dicom(path: Path, *, series_number: int, description: str, protocol: str) -> None:
    sop_instance_uid = generate_uid()
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = MRImageStorage
    file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()
    dataset = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    dataset.SOPClassUID = MRImageStorage
    dataset.SOPInstanceUID = sop_instance_uid
    dataset.PatientID = "TEST01"
    dataset.StudyInstanceUID = "1.2.826.0.1.3680043.10.543.1"
    dataset.SeriesInstanceUID = f"1.2.826.0.1.3680043.10.543.{series_number}"
    dataset.SeriesNumber = series_number
    dataset.SeriesDescription = description
    dataset.ProtocolName = protocol
    dataset.Modality = "MR"
    dataset.save_as(path, enforce_file_format=True)


class DicomSortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "scanner"
        self.source.mkdir()
        write_dicom(self.source / "one.dcm", series_number=1, description="T1w", protocol="MPRAGE")
        write_dicom(self.source / "two.dcm", series_number=2, description="T2w", protocol="SPACE")
        write_dicom(self.source / "three.dcm", series_number=3, description="Localizer", protocol="Scout")
        self.rules = {
            "version": 1,
            "rules": [
                {"name": "T1 description", "category": "anat", "match": {"SeriesDescription": "T1w"}},
                {"name": "T1 protocol", "category": "anat", "match": {"ProtocolName": "MPRAGE"}},
                {"name": "T2", "category": "anat", "match": {"SeriesDescription": "T2w"}},
            ],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preview_reports_matched_ambiguous_and_unmatched_series(self) -> None:
        preview = inspect_dicom(self.source, self.rules)
        self.assertEqual([row["status"] for row in preview], ["ambiguous", "matched", "unmatched"])
        self.assertEqual(preview[0]["category"], "_review_multiple_matches")
        self.assertEqual(preview[2]["category"], "_unmatched")

    def test_copy_preserves_source_and_places_series_in_reviewable_folders(self) -> None:
        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source.glob("*.dcm")
        }
        output = self.root / "sorted"
        result = sort_dicom(self.source, output, self.rules)
        self.assertEqual([row["status"] for row in result], ["ambiguous", "matched", "unmatched"])
        copied = list(output.rglob("*.dcm"))
        self.assertEqual(len(copied), 3)
        self.assertTrue(any("_review_multiple_matches" in path.parts for path in copied))
        self.assertTrue(any("_unmatched" in path.parts for path in copied))
        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source.glob("*.dcm")
        }
        self.assertEqual(before, after)
        for path in copied:
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                before[path.name],
            )

    def test_copy_refuses_nonempty_destination(self) -> None:
        output = self.root / "sorted"
        output.mkdir()
        (output / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not empty"):
            sort_dicom(self.source, output, self.rules)
        self.assertEqual((output / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_dicomdir_ids_rebase_to_selected_source_subfolder(self) -> None:
        source = self.root / "export" / "SUBJECT"
        series_dir = source / "SERIES"
        series_dir.mkdir(parents=True)
        image = series_dir / "IMAGE.DCM"
        write_dicom(image, series_number=1, description="T1w", protocol="MPRAGE")
        dicomdir = source.parent / "DICOMDIR"
        dicomdir.write_bytes(b"synthetic directory index")
        entries = [
            SimpleNamespace(DirectoryRecordType="PATIENT"),
            SimpleNamespace(
                DirectoryRecordType="STUDY",
                StudyInstanceUID="1.2.826.0.1.3680043.10.543.1",
            ),
            SimpleNamespace(
                DirectoryRecordType="SERIES",
                SeriesInstanceUID="1.2.826.0.1.3680043.10.543.1",
                SeriesDescription="T1w", Modality="MR", SeriesNumber="1",
            ),
            SimpleNamespace(
                DirectoryRecordType="IMAGE",
                ReferencedFileID=["SUBJECT", "SERIES", "IMAGE.DCM"],
            ),
        ]
        read_dicom = __import__("pydicom").dcmread

        def dcmread(path, *args, **kwargs):
            if Path(path) == dicomdir:
                return SimpleNamespace(DirectoryRecordSequence=entries)
            return read_dicom(path, *args, **kwargs)

        with patch("neuroflow.dicom_sort.pydicom.dcmread", side_effect=dcmread):
            preview = inspect_dicom(source, {
                "version": 1,
                "rules": [{
                    "name": "T1", "category": "anat",
                    "match": {"SeriesDescription": "^T1w$"},
                }],
            })

        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]["status"], "matched")
        self.assertEqual(preview[0]["files"], [image.resolve()])


if __name__ == "__main__":
    unittest.main()
