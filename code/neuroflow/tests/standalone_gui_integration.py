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
                    self.assertEqual(tuple(window.bids_subject_combo["values"]), ("sub-TEST01",))
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
                self.assertIn("dcm2bids was not found", showerror.call_args.args[1])
                self.assertIn("dcm2niix", showerror.call_args.args[1])
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
