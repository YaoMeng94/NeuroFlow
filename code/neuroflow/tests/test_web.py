import tempfile
import unittest
import re
import json
import html
import sys
import time
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError):
    TestClient = None

from neuroflow.core import JobSpec, Plan, SiteProfile
from neuroflow.jobs import JobStore


@unittest.skipUnless(TestClient, "web dependencies not installed")
class WebTests(unittest.TestCase):
    def test_multi_stage_job_detail_renders(self):
        from neuroflow.web import make_app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = JobStore(root / "jobs")
            spec = JobSpec(workflow="dwi", stage="preflight", stages=("preflight", "verify"),
                           input_dir=temp, output_dir=str(root / "derivatives"), participant="01")
            first = (sys.executable, "-c", "print('first')")
            second = (sys.executable, "-c", "print('second')")
            job_id = store.launch(Plan(spec, first, (), sequence=(first, second)))
            for _ in range(100):
                if store.get(job_id)["status"] == "completed":
                    break
                time.sleep(0.05)
            client = TestClient(make_app(SiteProfile(pipeline_root=temp), store))
            detail = client.get(f"/jobs/{job_id}")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("preflight + verify", detail.text)
            self.assertIn("2 / 2", detail.text)
            self.assertIn("first", detail.text)
            self.assertIn("second", detail.text)

    def test_bold_job_detail_shows_nested_freesurfer_activity(self):
        from neuroflow.web import make_app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "derivatives"
            store = JobStore(root / "jobs")
            spec = JobSpec(workflow="bold", stage="all", input_dir=temp,
                           output_dir=str(output), participant="05")
            job_id = store.launch(Plan(spec, (sys.executable, "-c", "pass"), ()))
            for _ in range(100):
                if store.get(job_id)["status"] == "completed":
                    break
                time.sleep(0.05)

            fmriprep_log = output / "mefmriprep" / "logs" / "fmriprep.log"
            recon_log = (output / ".work" / "mefmriprep" / "freesurfer_subjects"
                         / "sub-05" / "scripts" / "recon-all.log")
            fmriprep_log.parent.mkdir(parents=True)
            recon_log.parent.mkdir(parents=True)
            fmriprep_log.write_text("fMRIPrep is preparing echoes\n")
            recon_log.write_text("#GCMRL# registration is advancing\n")

            client = TestClient(make_app(SiteProfile(pipeline_root=temp), store))
            detail = client.get(f"/jobs/{job_id}")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("Pipeline activity", detail.text)
            self.assertIn("fMRIPrep is preparing echoes", detail.text)
            self.assertIn("#GCMRL# registration is advancing", detail.text)

    def test_nextflow_dwi_detail_renders_live_pipeline_output(self):
        from neuroflow.web import make_app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = JobStore(root / "jobs")
            spec = JobSpec(workflow="dwi", stage="all", execution_engine="nextflow",
                           input_dir=temp, output_dir=str(root / "derivatives"), participant="01")
            job_id = store.launch(Plan(spec, (sys.executable, "-c", "pass"), ()))
            for _ in range(100):
                if store.get(job_id)["status"] == "completed":
                    break
                time.sleep(0.05)
            (root / "jobs" / job_id / "pipeline.log").write_text("Live QSIPrep progress\n")
            client = TestClient(make_app(SiteProfile(pipeline_root=temp), store))
            detail = client.get(f"/jobs/{job_id}")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("Pipeline activity", detail.text)
            self.assertIn("Live QSIPrep progress", detail.text)

    def test_home_inventory_and_csrf(self):
        from neuroflow.web import make_app

        with tempfile.TemporaryDirectory() as temp:
            app = make_app(SiteProfile(pipeline_root=temp), JobStore(Path(temp) / "jobs"))
            client = TestClient(app)
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("DICOM to BIDS", home.text)
            self.assertIn('value="Brainnetome246Ext,Glasser360"', home.text)
            self.assertIn("leave blank to skip surface reconstruction in fMRIPrep", home.text)
            self.assertEqual(client.get("/inventory").status_code, 200)
            self.assertEqual(client.post("/plan", data={}).status_code, 403)

    def test_dicom_inventory_plan_does_not_require_subject_label(self):
        from neuroflow.web import make_app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "dicom"
            source.mkdir()
            helper = root / "dcm2bids_helper"
            helper.write_text("#!/bin/sh\nexit 0\n")
            helper.chmod(0o755)
            dcm2niix = root / "dcm2niix"
            dcm2niix.write_text("#!/bin/sh\nexit 0\n")
            dcm2niix.chmod(0o755)
            app = make_app(SiteProfile(
                pipeline_root=temp, dcm2niix=str(dcm2niix),
                dcm2bids_helper=str(helper),
            ), JobStore(root / "jobs"))
            client = TestClient(app)
            home = client.get("/")
            token = re.search(r'name="csrf" value="([^\"]+)"', home.text).group(1)
            response = client.post("/plan", data={
                "csrf": token, "workflow": "dicom", "stages": ["inventory"],
                "input_dir": str(source), "output_dir": str(root / "inventory"),
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn("neuroflow.dicom_inventory", response.text)
            self.assertIn("Study inventory", response.text)
            self.assertNotIn("sub-inventory", response.text)

    def test_bids_plan_and_manual_dicom_mapping(self):
        from neuroflow.web import make_app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bids = root / "bids"
            (bids / "sub-01").mkdir(parents=True)
            (bids / "dataset_description.json").write_text('{"Name":"test"}')
            pipeline = root / "pipelines" / "dwi"
            pipeline.mkdir(parents=True)
            (pipeline / "run_dwi_bidsapp.sh").write_text("#!/bin/sh\nexit 0\n")
            sidecars = root / "inventory"
            sidecars.mkdir()
            (sidecars / "series.json").write_text(json.dumps({
                "SeriesNumber": 10, "SeriesDescription": "3dT1w_mpr_sag_1mm", "PatientName": "DoNotDisplay",
                "AcquisitionTime": "101500.25", "AcquisitionDuration": 612.0,
                "ImageType": ["ORIGINAL", "PRIMARY"], "ImageTypeText": ["NORM", "DIS2D"],
                "NonlinearGradientCorrection": False, "EchoNumber": 2,
                "EchoTime": 0.029, "RepetitionTime": 1.2,
            }))
            (root / "study_manifest.json").write_text(json.dumps({
                "version": 1,
                "groups": [{"id": "study-01", "label": "2026-10-12 10:15 (study 01)"}],
                "study_uids": {"study-01": "1.2.3"},
                "sidecars": {"series.json": "study-01"},
            }))
            app = make_app(SiteProfile(pipeline_root=str(root / "pipelines")), JobStore(root / "jobs"))
            client = TestClient(app)
            home = client.get("/")
            token = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
            response = client.post("/plan", data={
                "csrf": token, "workflow": "dwi", "stage": "preflight",
                "input_dir": str(bids), "output_dir": str(root / "derivatives"),
                "participant": "01", "nprocs": "4", "omp_nthreads": "2", "mem_mb": "4096",
                "gpu": "none", "atlases": "Brainnetome246Ext",
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn("run_dwi_bidsapp.sh", response.text)
            self.assertFalse((root / "derivatives").exists())
            spec_value = re.search(r'name="spec" value="([^"]+)"', response.text).group(1)
            edit_response = client.post("/edit", data={
                "csrf": token, "spec": html.unescape(spec_value),
            })
            self.assertEqual(edit_response.status_code, 200)
            self.assertIn(f'value="{bids}"', edit_response.text)
            self.assertIn('const initialStages = ["preflight"]', edit_response.text)
            sequence = client.post("/plan", data={
                "csrf": token, "workflow": "dwi", "stages": ["verify", "preflight"],
                "input_dir": str(bids), "output_dir": str(root / "derivatives"),
                "participant": "01", "nprocs": "4", "omp_nthreads": "2", "mem_mb": "4096",
            })
            self.assertEqual(sequence.status_code, 200)
            self.assertIn("preflight + verify", sequence.text)
            self.assertEqual(sequence.text.count("run_dwi_bidsapp.sh"), 2)
            inventory = client.get("/inventory", params={"path": str(sidecars)})
            self.assertEqual(inventory.status_code, 200)
            self.assertNotIn("DoNotDisplay", inventory.text)
            self.assertIn("101500.25", inventory.text)
            self.assertIn("ORIGINAL, PRIMARY", inventory.text)
            self.assertIn("NORM, DIS2D", inventory.text)
            self.assertIn("Nonlinear gradient correction: False", inventory.text)
            self.assertIn("Default T1w (NORM)", inventory.text)
            self.assertRegex(inventory.text, r'<option value="anat" selected>anat</option>')
            self.assertRegex(inventory.text, r'<input name="row_0_suffix" value="T1w"')
            self.assertIn("Echo 2: 0.029 s", inventory.text)
            saved = client.post("/inventory/mapping", data={
                "csrf": token, "path": str(sidecars), "source_group": "study-01",
                "row_0_datatype": "anat",
                "row_0_suffix": "T1w", "row_0_id": "t1",
            })
            self.assertEqual(saved.status_code, 200)
            self.assertIn("T1w", saved.text)
            draft = next((root / "jobs" / "mappings").glob("*.json"))
            description = json.loads(draft.read_text())["descriptions"][0]
            self.assertEqual(description["suffix"], "T1w")
            self.assertEqual(description["criteria"]["AcquisitionTime"], "101500.25")
            self.assertEqual(json.loads(draft.read_text())["NeuroFlowStudyId"], "study-01")

    def test_inventory_filters_study_group_before_mapping(self):
        from neuroflow.web import make_app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sidecars = root / "inventory"
            sidecars.mkdir()
            for name, acquisition_time in (("series1.json", "09:00:00"),
                                           ("series2.json", "10:30:00")):
                (sidecars / name).write_text(json.dumps({
                    "SeriesNumber": 10, "SeriesDescription": "T1w",
                    "AcquisitionTime": acquisition_time,
                }))
            (root / "study_manifest.json").write_text(json.dumps({
                "version": 1,
                "groups": [{"id": "study-01", "label": "2026-10-12 08:56 (study 01)"},
                           {"id": "study-02", "label": "2026-10-12 10:27 (study 02)"}],
                "study_uids": {"study-01": "1.2.3", "study-02": "1.2.4"},
                "sidecars": {"series1.json": "study-01", "series2.json": "study-02"},
            }))
            client = TestClient(make_app(SiteProfile(pipeline_root=temp),
                                         JobStore(root / "jobs")))
            response = client.get("/inventory", params={
                "path": str(sidecars), "source_group": "study-02",
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn("2026-10-12 10:27 (study 02)", response.text)
            self.assertIn("10:30:00", response.text)
            self.assertNotIn("09:00:00", response.text)


if __name__ == "__main__":
    unittest.main()
