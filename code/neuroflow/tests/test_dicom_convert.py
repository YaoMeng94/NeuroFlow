from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from neuroflow.dicom_convert import main


class DicomConvertTests(unittest.TestCase):
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
