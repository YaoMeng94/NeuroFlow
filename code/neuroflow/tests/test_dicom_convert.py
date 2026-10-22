from __future__ import annotations

import contextlib
import gzip
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from neuroflow.dicom_convert import main
from neuroflow.dicom_inventory import build_manifest
from neuroflow.mapping import build_mapping


def write_mr_image(
    path: Path, *, study_uid: str, series_uid: str, instance: int,
    study_time: str = "101500.25", series_description: str = "NeuroFlow Synthetic T1",
) -> None:
    from pydicom import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

    sop_uid = generate_uid()
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = MRImageStorage
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()
    dataset = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    dataset.SOPClassUID = MRImageStorage
    dataset.SOPInstanceUID = sop_uid
    dataset.PatientName = "NeuroFlow^Synthetic"
    dataset.PatientID = "NFTEST"
    dataset.StudyInstanceUID = study_uid
    dataset.SeriesInstanceUID = series_uid
    dataset.StudyDate = "20260101"
    dataset.StudyTime = study_time
    dataset.SeriesNumber = 1
    dataset.SeriesDescription = series_description
    dataset.ProtocolName = series_description
    dataset.AcquisitionTime = study_time
    dataset.InstanceNumber = instance
    dataset.Modality = "MR"
    dataset.Manufacturer = "Siemens"
    dataset.MagneticFieldStrength = 3.0
    dataset.MRAcquisitionType = "2D"
    dataset.PatientPosition = "HFS"
    dataset.RepetitionTime = 10.0
    dataset.Rows = 4
    dataset.Columns = 4
    dataset.PixelSpacing = [1.0, 1.0]
    dataset.SliceThickness = 1.0
    dataset.SpacingBetweenSlices = 1.0
    dataset.ImagePositionPatient = [0.0, 0.0, float(instance - 1)]
    dataset.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.BitsAllocated = 16
    dataset.BitsStored = 16
    dataset.HighBit = 15
    dataset.PixelRepresentation = 0
    dataset.PixelData = bytes([instance] * (dataset.Rows * dataset.Columns * 2))
    dataset.save_as(path, enforce_file_format=True)


class DicomConvertTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("dcm2bids") and shutil.which("dcm2niix"),
        "dcm2bids and dcm2niix are required for the real-converter smoke test",
    )
    def test_real_dcm2bids_converts_selected_synthetic_study_to_bids(self) -> None:
        from pydicom.uid import generate_uid

        with tempfile.TemporaryDirectory(prefix="neuroflow-real-convert-test-") as temporary:
            root = Path(temporary)
            source = root / "scanner-export"
            selected_series = source / "study-a" / "t1"
            unrelated_series = source / "study-b" / "t1"
            selected_series.mkdir(parents=True)
            unrelated_series.mkdir(parents=True)
            study_a, study_b = generate_uid(), generate_uid()
            series_a, series_b = generate_uid(), generate_uid()
            selected_paths = [selected_series / f"image-{index}.dcm" for index in (1, 2)]
            for index, path in enumerate(selected_paths, start=1):
                write_mr_image(path, study_uid=study_a, series_uid=series_a, instance=index)
            write_mr_image(
                unrelated_series / "image-1.dcm", study_uid=study_b,
                series_uid=series_b, instance=1, study_time="113000.25",
                series_description="NeuroFlow Unrelated T1",
            )
            raw_hashes = {path: path.read_bytes() for path in [
                *selected_paths, unrelated_series / "image-1.dcm",
            ]}

            inventory = root / "inventory"
            inventory.mkdir()
            manifest = inventory / "study_manifest.json"
            helper_dir = inventory / "helper"
            helper = shutil.which("dcm2bids_helper")
            self.assertIsNotNone(helper)
            subprocess.run(
                [helper or "dcm2bids_helper", "-d", str(source), "-o", str(helper_dir)],
                check=True, capture_output=True, text=True,
            )
            public_manifest, uid_index = build_manifest(source, helper_dir, workers=1)
            manifest.write_text(json.dumps(public_manifest), encoding="utf-8")
            (inventory / "study_uid_index.json").write_text(
                json.dumps(uid_index), encoding="utf-8",
            )

            target_sidecar = next(
                path for path in helper_dir.rglob("*.json")
                if json.loads(path.read_text()).get("SeriesDescription") == "NeuroFlow Synthetic T1"
            )
            selected_study = public_manifest["sidecars"][target_sidecar.name]
            self.assertNotEqual(selected_study, "unresolved")
            rows = [json.loads(target_sidecar.read_text())]
            rows[0]["source_group"] = selected_study
            generated_config = build_mapping(
                rows,
                {"row_0_datatype": "anat", "row_0_suffix": "T1w"},
                study_id=selected_study,
                manifest_path=str(manifest),
            )
            config = root / "mapping.json"
            config.write_text(json.dumps(generated_config), encoding="utf-8")
            output = root / "bids-output"

            result = main([
                shutil.which("dcm2bids") or "dcm2bids", str(source),
                str(output), "NF01", "", str(config),
            ])

            self.assertEqual(result, 0)
            nifti = output / "sub-NF01" / "anat" / "sub-NF01_T1w.nii.gz"
            sidecar = nifti.with_suffix("").with_suffix(".json")
            self.assertTrue(nifti.is_file())
            self.assertTrue(sidecar.is_file())
            with gzip.open(nifti, "rb") as stream:
                header = stream.read(56)
            dimensions = struct.unpack("<8h", header[40:56])
            self.assertEqual(dimensions[:4], (3, 4, 4, 2))
            self.assertEqual(list((output / "sub-NF01" / "anat").glob("*.nii.gz")), [nifti])
            self.assertEqual(json.loads(sidecar.read_text())["SeriesNumber"], 1)
            self.assertTrue((output / "dataset_description.json").is_file())
            self.assertTrue((root / "dcm2bids_intermediates" / output.name / "log").is_dir())
            for path, original_bytes in raw_hashes.items():
                self.assertEqual(path.read_bytes(), original_bytes)

    def test_reviewed_study_is_staged_and_finalized_without_changing_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="neuroflow-convert-test-") as temporary:
            root = Path(temporary)
            source = root / "scanner-export"
            (source / "study-a").mkdir(parents=True)
            (source / "study-b").mkdir()
            selected = source / "study-a" / "t1.dcm"
            other_study = source / "study-b" / "bold.dcm"
            selected.write_bytes(b"selected DICOM fixture")
            other_study.write_bytes(b"different study fixture")
            original_selected = selected.read_bytes()
            original_other = other_study.read_bytes()

            inventory = root / "inventory"
            inventory.mkdir()
            manifest = inventory / "study_manifest.json"
            (inventory / "study_uid_index.json").write_text(json.dumps({
                "source_root": str(source.resolve()),
                "studies": {
                    "study-01": {"uid": "1.2.3", "files": ["study-a/t1.dcm"]},
                    "study-02": {"uid": "1.2.4", "files": ["study-b/bold.dcm"]},
                },
            }), encoding="utf-8")
            config = root / "mapping.json"
            config.write_text(json.dumps({
                "descriptions": [{
                    "datatype": "anat", "suffix": "T1w", "id": "t1",
                    "criteria": {"SeriesNumber": 1},
                }],
                "NeuroFlowStudyId": "study-01",
                "NeuroFlowManifest": str(manifest),
            }), encoding="utf-8")

            record = root / "converter-call.json"
            converter = root / "dcm2bids-fixture"
            converter.write_text(textwrap.dedent(f"""\
                #!{sys.executable}
                import json
                import sys
                from pathlib import Path

                args = sys.argv[1:]
                source = Path(args[args.index('-d') + 1])
                output = Path(args[args.index('-o') + 1])
                participant = args[args.index('-p') + 1]
                session = args[args.index('-s') + 1] if '-s' in args else ''
                config = json.loads(Path(args[args.index('-c') + 1]).read_text())
                Path({str(record)!r}).write_text(json.dumps({{
                    'files': sorted(path.name for path in source.iterdir()),
                    'participant': participant,
                    'session': session,
                    'config': config,
                }}))

                subject = output / f'sub-{{participant}}'
                if session:
                    subject /= f'ses-{{session}}'
                anat = subject / 'anat'
                anat.mkdir(parents=True)
                stem = f'sub-{{participant}}'
                if session:
                    stem += f'_ses-{{session}}'
                stem += '_T1w'
                (anat / f'{{stem}}.nii.gz').write_bytes(b'fixture NIfTI')
                (anat / f'{{stem}}.json').write_text(json.dumps({{'SeriesNumber': 1}}))
                intermediate = output / 'tmp_dcm2bids' / f'sub-{{participant}}'
                intermediate.mkdir(parents=True)
                (intermediate / 'converter-marker.txt').write_text('kept')
            """), encoding="utf-8")
            converter.chmod(converter.stat().st_mode | 0o111)

            output = root / "bids-output"
            result = main([
                str(converter), str(source), str(output), "01", "02", str(config),
            ])

            self.assertEqual(result, 0)
            call = json.loads(record.read_text(encoding="utf-8"))
            staged_dicoms = [name for name in call["files"] if name.endswith(".dcm")]
            self.assertEqual(staged_dicoms, ["00000001.dcm"])
            self.assertIn("dcm2bids_config.json", call["files"])
            self.assertEqual((call["participant"], call["session"]), ("01", "02"))
            self.assertEqual(call["config"]["descriptions"][0]["id"], "t1")
            self.assertNotIn("NeuroFlowStudyId", call["config"])

            subject = output / "sub-01" / "ses-02"
            self.assertTrue((subject / "anat" / "sub-01_ses-02_T1w.nii.gz").is_file())
            dataset = json.loads((output / "dataset_description.json").read_text())
            self.assertEqual(dataset["BIDSVersion"], "1.9.0")
            self.assertEqual(dataset["GeneratedBy"][0]["Name"], "dcm2bids")
            archive = root / "dcm2bids_intermediates" / output.name / "sub-01"
            self.assertEqual((archive / "converter-marker.txt").read_text(), "kept")
            self.assertFalse((output / "tmp_dcm2bids").exists())
            self.assertEqual(selected.read_bytes(), original_selected)
            self.assertEqual(other_study.read_bytes(), original_other)

    def test_refuses_manifest_from_a_different_source_before_running_converter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="neuroflow-convert-source-test-") as temporary:
            root = Path(temporary)
            source = root / "scanner-export"
            source.mkdir()
            inventory = root / "inventory"
            inventory.mkdir()
            manifest = inventory / "study_manifest.json"
            (inventory / "study_uid_index.json").write_text(json.dumps({
                "source_root": str(root / "other-export"),
                "studies": {"study-01": {"uid": "1.2.3", "files": ["t1.dcm"]}},
            }), encoding="utf-8")
            config = root / "mapping.json"
            config.write_text(json.dumps({
                "descriptions": [{"datatype": "anat", "suffix": "T1w", "criteria": {"SeriesNumber": 1}}],
                "NeuroFlowStudyId": "study-01", "NeuroFlowManifest": str(manifest),
            }), encoding="utf-8")
            marker = root / "converter-was-run"
            converter = root / "converter"
            converter.write_text(
                f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            converter.chmod(converter.stat().st_mode | 0o111)

            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main([
                    str(converter), str(source), str(root / "bids"), "01", "", str(config),
                ])

            self.assertFalse(marker.exists())
            self.assertFalse((root / "bids").exists())


if __name__ == "__main__":
    unittest.main()
