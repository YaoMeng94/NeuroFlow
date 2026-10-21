import os
import subprocess
import sys
import tempfile
import time
import builtins
import importlib
import types
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from neuroflow.core import JobSpec, Plan
from neuroflow.jobs import JobStore


class JobTests(unittest.TestCase):
    def test_modality_jobs_reject_native_windows_execution(self):
        from neuroflow.jobs import _validate_execution_host

        spec = JobSpec(workflow="bold", stage="all", input_dir=".", output_dir=".", participant="01")
        with self.assertRaisesRegex(RuntimeError, "POSIX shell"):
            _validate_execution_host(Plan(spec, ("bash", "run_bold_bidsapp.sh"), ()), "win32")
        _validate_execution_host(Plan(spec, ("python.exe", "-c", "pass"), ()), "win32")
        with self.assertRaisesRegex(RuntimeError, "POSIX shell"):
            _validate_execution_host(
                Plan(JobSpec(**{**spec.as_dict(), "execution_engine": "nextflow"}), ("nextflow", "run"), ()),
                "win32",
            )
        _validate_execution_host(Plan(spec, ("bash", "run_bold_bidsapp.sh"), ()), "darwin")
        dicom = JobSpec(workflow="dicom", stage="inventory", input_dir=".", output_dir=".", participant="inventory")
        _validate_execution_host(Plan(dicom, ("python.exe", "-c", "pass"), ()), "win32")

    def test_windows_job_status_uses_read_only_process_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JobStore(Path(temp) / "jobs")
            job_id = "a" * 32
            job_dir = store.root / job_id
            job_dir.mkdir()
            (job_dir / "job.json").write_text('{"id":"' + job_id + '","pid":1234}')
            with patch("neuroflow.jobs.sys.platform", "win32"), \
                    patch("neuroflow.jobs._windows_process_running", return_value=True) as probe, \
                    patch("neuroflow.jobs.os.kill", side_effect=AssertionError("must not signal Windows PID")):
                self.assertEqual(store.get(job_id)["status"], "running")
            probe.assert_called_once_with(1234)

            with patch("neuroflow.jobs.sys.platform", "win32"), \
                    patch("neuroflow.jobs._windows_process_running", return_value=False):
                self.assertEqual(store.get(job_id)["status"], "interrupted")

    def test_worker_process_options_are_platform_specific(self):
        from neuroflow.jobs import _worker_process_options

        posix = _worker_process_options("linux")
        self.assertTrue(posix["start_new_session"])
        self.assertNotIn("creationflags", posix)

        windows = _worker_process_options("win32")
        self.assertNotIn("start_new_session", windows)
        self.assertIn("creationflags", windows)
        self.assertTrue(windows["close_fds"])

    def test_output_lock_is_nonblocking_for_a_second_open_file(self):
        from neuroflow.jobs import _try_acquire_lock

        with tempfile.TemporaryDirectory() as temp:
            lock_path = Path(temp) / "subject.lock"
            with lock_path.open("a+b") as first, lock_path.open("a+b") as second:
                self.assertTrue(_try_acquire_lock(first))
                self.assertFalse(_try_acquire_lock(second))

    def test_windows_output_lock_uses_nonblocking_msvcrt_lock(self):
        from neuroflow.jobs import _try_acquire_lock

        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_NBLCK = 1
        lock_calls = []

        def locking(fd, mode, byte_count):
            lock_calls.append((fd, mode, byte_count))
            if len(lock_calls) > 1:
                raise OSError("lock is already held")

        fake_msvcrt.locking = locking
        with tempfile.TemporaryDirectory() as temp:
            lock_path = Path(temp) / "subject.lock"
            with lock_path.open("a+b") as first, lock_path.open("a+b") as second:
                with patch("os.name", "nt"), patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                    self.assertTrue(_try_acquire_lock(first))
                    self.assertFalse(_try_acquire_lock(second))
        self.assertEqual([call[1:] for call in lock_calls], [(1, 1), (1, 1)])

    def test_job_module_imports_without_posix_fcntl_on_windows(self):
        import neuroflow.jobs as jobs_module

        original_import = builtins.__import__
        children = jobs_module._CHILDREN

        def windows_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ModuleNotFoundError("fcntl is not available on Windows")
            return original_import(name, *args, **kwargs)

        try:
            with patch("os.name", "nt"), patch.dict(sys.modules, {"msvcrt": types.ModuleType("msvcrt")}), \
                    patch("builtins.__import__", side_effect=windows_import):
                importlib.reload(jobs_module)
        finally:
            importlib.reload(jobs_module)
            jobs_module._CHILDREN = children

    def test_concurrent_jobs_for_same_subject_and_output_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "first-job-started"
            spec = JobSpec(workflow="dwi", stage="verify", input_dir=temp,
                           output_dir=str(root / "derivatives"), participant="01")
            command = (sys.executable, "-c",
                       f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(1.5)")
            store = JobStore(root / "jobs")
            first = store.launch(Plan(spec, command, ()))
            for _ in range(100):
                if marker.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(marker.exists(), f"first job did not start; worker log: {store.log(first)}")

            second = store.launch(Plan(spec, command, ()))
            for _ in range(100):
                job = store.get(second)
                if job["status"] in {"completed", "failed", "interrupted"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job["status"], "failed")
            self.assertIn("Another job for this workflow", store.log(second))

            for _ in range(100):
                if store.get(first)["status"] == "completed":
                    break
                time.sleep(0.05)
            self.assertEqual(store.get(first)["status"], "completed")

    def test_worker_path_prefers_the_controller_python_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = JobSpec(workflow="dwi", stage="verify", input_dir=temp,
                           output_dir=str(root / "derivatives"), participant="01")
            store = JobStore(root / "jobs")
            with patch("neuroflow.jobs.subprocess.Popen", wraps=subprocess.Popen) as popen:
                job_id = store.launch(Plan(spec, (sys.executable, "-c", "pass"), ()))
                environment = popen.call_args.kwargs["env"]
            expected_bin = str(Path(sys.executable).parent)
            self.assertEqual(environment["PATH"].split(os.pathsep)[0], expected_bin)
            for _ in range(100):
                if store.get(job_id)["status"] in {"completed", "failed", "interrupted"}:
                    break
                time.sleep(0.05)
            self.assertEqual(store.get(job_id)["status"], "completed")

    def test_multi_stage_job_stops_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "should-not-run"
            spec = JobSpec(workflow="dwi", stage="preflight", stages=("preflight", "verify"),
                           input_dir=temp, output_dir=str(root / "derivatives"), participant="01")
            first = (sys.executable, "-c", "raise SystemExit(7)")
            second = (sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()")
            store = JobStore(root / "jobs")
            job_id = store.launch(Plan(spec, first, (), sequence=(first, second)))
            for _ in range(100):
                job = store.get(job_id)
                if job["status"] in {"completed", "failed", "interrupted"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["exit_code"], 7)
            self.assertEqual(job["completed_stages"], 0)
            self.assertFalse(marker.exists())
            self.assertIn("remaining stages skipped", store.log(job_id))

    def test_worker_spawn_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = JobSpec(workflow="dwi", stage="verify", input_dir=temp,
                           output_dir=str(root / "derivatives"), participant="01")
            store = JobStore(root / "jobs")
            with patch("neuroflow.jobs.subprocess.Popen", side_effect=OSError("spawn failed")):
                with self.assertRaisesRegex(RuntimeError, "spawn failed"):
                    store.launch(Plan(spec, ("true",), ()))
            self.assertEqual(store.list()[0]["status"], "failed")

    def test_detached_job_persists_result_and_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = JobSpec(workflow="dwi", stage="verify", input_dir=temp,
                           output_dir=str(root / "derivatives"), participant="01")
            command = (sys.executable, "-c", "print('verified')")
            store = JobStore(root / "jobs")
            job_id = store.launch(Plan(spec, command, ()))
            for _ in range(100):
                job = store.get(job_id)
                if job["status"] in {"completed", "failed", "interrupted"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["exit_code"], 0)
            self.assertIn("verified", JobStore(root / "jobs").log(job_id))

    def test_nextflow_job_uses_persistent_launch_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = JobStore(root / "jobs")
            for workflow in ("anat", "bold", "dwi"):
                with self.subTest(workflow=workflow):
                    output = root / f"derivatives-{workflow}"
                    spec = JobSpec(workflow=workflow, stage="all", execution_engine="nextflow",
                                   input_dir=temp, output_dir=str(output), participant="01")
                    command = (sys.executable, "-c", "import os; print(os.getcwd()); print(os.environ['NEUROFLOW_TASK_LOG'])")
                    job_id = store.launch(Plan(spec, command, ()))
                    for _ in range(100):
                        job = store.get(job_id)
                        if job["status"] in {"completed", "failed", "interrupted"}:
                            break
                        time.sleep(0.05)
                    expected = output / ".neuroflow" / "launch"
                    self.assertEqual(job["status"], "completed")
                    self.assertIn(str(expected), store.log(job_id))
                    self.assertIn(str(root / "jobs" / job_id / "pipeline.log"), store.log(job_id))

    def test_pipeline_log_is_read_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = JobSpec(workflow="bold", stage="all", execution_engine="nextflow",
                           input_dir=temp, output_dir=str(root / "derivatives"), participant="01")
            store = JobStore(root / "jobs")
            job_id = store.launch(Plan(spec, (sys.executable, "-c", "pass"), ()))
            for _ in range(100):
                if store.get(job_id)["status"] == "completed":
                    break
                time.sleep(0.05)
            (root / "jobs" / job_id / "pipeline.log").write_text("fMRIPrep is running\n")
            self.assertEqual(store.pipeline_log(job_id), "fMRIPrep is running\n")

    def test_bold_pipeline_log_includes_nested_live_freesurfer_activity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "derivatives"
            spec = JobSpec(workflow="bold", stage="all", input_dir=temp,
                           output_dir=str(output), participant="05")
            store = JobStore(root / "jobs")
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
            fmriprep_log.write_text("fMRIPrep started\n")
            recon_log.write_text("#GCMRL# registration is advancing\n")

            live_log = store.pipeline_log(job_id)
            self.assertIn("fMRIPrep started", live_log)
            self.assertIn("#GCMRL# registration is advancing", live_log)

    def test_dwi_pipeline_log_reads_the_dwiprep_log_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "derivatives"
            spec = JobSpec(workflow="dwi", stage="qsiprep", input_dir=temp,
                           output_dir=str(output), participant="05")
            store = JobStore(root / "jobs")
            job_id = store.launch(Plan(spec, (sys.executable, "-c", "pass"), ()))
            for _ in range(100):
                if store.get(job_id)["status"] == "completed":
                    break
                time.sleep(0.05)

            log = output / "dwiprep" / "logs" / "qsiprep.log"
            log.parent.mkdir(parents=True)
            log.write_text("QSIPrep is running\n")
            self.assertIn("QSIPrep is running", store.pipeline_log(job_id))

    def test_pipeline_activity_includes_live_worker_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = JobSpec(workflow="bold", stage="all", input_dir=temp,
                           output_dir=str(root / "derivatives"), participant="05")
            command = (sys.executable, "-c", "import time; time.sleep(2)")
            store = JobStore(root / "jobs")
            job_id = store.launch(Plan(spec, command, ()))
            time.sleep(0.15)

            pid = store.get(job_id)["pid"]
            process_list = f"123 {pid} {pid} 00:01 14.0 0.1 python {sys.executable} -m neuroflow.worker\n"
            with patch("neuroflow.jobs.subprocess.run", return_value=Mock(stdout=process_list)):
                activity = store.pipeline_log(job_id)
            self.assertIn("Active worker processes", activity)
            self.assertIn("python", activity.lower())


if __name__ == "__main__":
    unittest.main()
