from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_dicom_sort import write_dicom


class StandaloneGuiIntegrationTests(unittest.TestCase):
    def test_convert_all_subjects_stages_one_participant_at_a_time(self) -> None:
        import tkinter as tk

        from neuroflow.standalone_gui import ALL_SUBJECTS_OPTION, SorterWindow

        with tempfile.TemporaryDirectory(prefix="neuroflow-all-subjects-test-") as temp:
            root_path = Path(temp)
            source = root_path / "scanner"
            sorted_root = root_path / "scanner_sorted"
            output = root_path / "bids"
            config = root_path / "config.json"
            source.mkdir()
            sorted_root.mkdir()
            config.write_text("{}", encoding="utf-8")
            subject_files = {}
            for label, series_number in (("sub-TEST01", 1), ("sub-TEST02", 2)):
                subject_dir = sorted_root / label / "anat" / f"series-{series_number}"
                subject_dir.mkdir(parents=True)
                dicom = subject_dir / "image.dcm"
                write_dicom(
                    dicom, series_number=series_number,
                    description="T1w", protocol="MPRAGE",
                )
                subject_files[label] = dicom.read_bytes()

            root = tk.Tk()
            try:
                with patch.dict(os.environ, {
                    "XDG_CONFIG_HOME": str(root_path / "config"),
                    "APPDATA": str(root_path / "appdata"),
                    "HOME": str(root_path / "home"),
                }), patch.object(Path, "home", return_value=root_path / "home"):
                    window = SorterWindow(root)
                window.source_var.set(str(source))
                window.output_var.set(str(sorted_root))
                window.bids_output_var.set(str(output))
                window.bids_config_var.set(str(config))
                window.bids_subject_var.set(ALL_SUBJECTS_OPTION)
                window.rows = [{"study": label} for label in subject_files]
                staged_paths = []
                participants = []

                def run_converter(command, **kwargs):
                    participant = command[command.index("-p") + 1]
                    staged = Path(command[command.index("-d") + 1])
                    files = list(staged.rglob("*.dcm"))
                    self.assertEqual(len(files), 1)
                    staged_paths.append(staged)
                    participants.append(participant)
                    return __import__("subprocess").CompletedProcess(command, 0, "", "")

                with patch("neuroflow.standalone_gui.shutil.which", return_value="dcm2bids"), \
                     patch("neuroflow.standalone_gui.subprocess.run", side_effect=run_converter), \
                     patch("neuroflow.standalone_gui.finalize_bids") as finalize, \
                     patch("neuroflow.standalone_gui.messagebox.askyesno", return_value=True), \
                     patch("neuroflow.standalone_gui.messagebox.showinfo") as showinfo, \
                     patch("neuroflow.standalone_gui.messagebox.showerror") as showerror:
                    window._convert_bids()
                    self._wait_until_idle(root, window)

                showerror.assert_not_called()
                showinfo.assert_called_once()
                self.assertEqual(participants, ["TEST01", "TEST02"])
                self.assertEqual([call.args[1:] for call in finalize.call_args_list], [
                    ("TEST01", "", {}), ("TEST02", "", {}),
                ])
                self.assertTrue(all(not path.exists() for path in staged_paths))
                for label, expected_bytes in subject_files.items():
                    actual = next((sorted_root / label).rglob("*.dcm")).read_bytes()
                    self.assertEqual(actual, expected_bytes)
            finally:
                root.destroy()

    def test_preview_review_copy_and_bids_preflight(self) -> None:
        import tkinter as tk

        from neuroflow.standalone_gui import SorterWindow

        with tempfile.TemporaryDirectory(prefix="neuroflow-tk-test-") as temp:
            root_path = Path(temp)
            config_home = root_path / "config"
            source = root_path / "scanner"
            output = root_path / "scanner_sorted"
            source.mkdir()
            write_dicom(source / "matched.dcm", series_number=1, description="T1w", protocol="MPRAGE")
            write_dicom(source / "unmatched.dcm", series_number=2, description="Localizer", protocol="Scout")
            before = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source.glob("*.dcm")
            }
            rules = root_path / "rules.json"
            rules.write_text(json.dumps({
                "version": 1,
                "rules": [{
                    "name": "T1 anatomy",
                    "category": "anat",
                    "match": {"SeriesDescription": "^T1w$"},
                }],
            }), encoding="utf-8")

            root = tk.Tk()
            try:
                with patch.dict(os.environ, {
                    "XDG_CONFIG_HOME": str(config_home),
                    "APPDATA": str(root_path / "appdata"),
                    "HOME": str(root_path / "home"),
                }), patch.object(Path, "home", return_value=root_path / "home"):
                    window = SorterWindow(root)
                root.update()
                window.source_var.set(str(source))
                window.output_var.set(str(output))
                window.rules_var.set(str(rules))

                with patch("neuroflow.standalone_gui.messagebox.showerror") as unexpected_error:
                    window.preview_button.invoke()
                    self._wait_until_idle(root, window)
                    self.assertEqual([row["status"] for row in window.rows], ["matched", "unmatched"])
                    self.assertEqual(tuple(window.bids_subject_combo["values"]), ("All subjects", "sub-TEST01"))
                    self.assertTrue(Path(window.bids_config_var.get()).is_file())
                    self.assertEqual(str(window.copy_button["state"]), "disabled")

                    with patch("neuroflow.standalone_gui.messagebox.askyesno", return_value=True) as confirm, \
                         patch("neuroflow.standalone_gui.messagebox.showinfo") as showinfo:
                        window.copy_button.invoke()
                        confirm.assert_not_called()
                        self.assertFalse(output.exists())

                        window.review_check.invoke()
                        self.assertTrue(window.review_var.get())
                        self.assertEqual(str(window.copy_button["state"]), "normal")
                        window.copy_button.invoke()
                        confirm.assert_called_once()
                        self._wait_until_idle(root, window)
                        showinfo.assert_called_once()
                    unexpected_error.assert_not_called()

                copied = list(output.rglob("*.dcm"))
                self.assertEqual(len(copied), 2)
                self.assertTrue(any("_unmatched" in path.parts for path in copied))
                for path in copied:
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before[path.name])
                after = {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in source.glob("*.dcm")
                }
                self.assertEqual(before, after)

                window.bids_output_var.set(str(root_path / "bids"))
                with patch("neuroflow.standalone_gui.shutil.which", return_value=None), \
                     patch("neuroflow.standalone_gui.messagebox.showerror") as showerror:
                    window._convert_bids()
                message = showerror.call_args.args[1]
                self.assertIn("dcm2bids was not found in the app or on PATH", message)
                self.assertIn("Reinstall the app", message)
                self.assertIn("install dcm2bids and dcm2niix", message)
            finally:
                root.destroy()

    @staticmethod
    def _wait_until_idle(root, window, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while window.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        root.update()
        if window.busy:
            raise AssertionError("Sorter operation did not finish before the timeout")
        if window.status_var.get() == "The operation could not be completed.":
            raise AssertionError("Sorter operation failed: " + window.status_var.get())


if __name__ == "__main__":
    unittest.main()
