#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from pipelines.bold.compute_bold_qc import select_comparison_echo
from pipelines.bold.verify_bold_outputs import comparison_tsnr_errors


class ComparisonEchoTests(unittest.TestCase):
    def test_selects_echo_closest_to_target_te(self):
        echo, method = select_comparison_echo(
            [0.012, 0.02974, 0.047, 0.064, 0.081], 5, 30.0
        )
        self.assertEqual((echo, method), (2, "closest-TE"))

    def test_uses_explicit_echo_when_available(self):
        echo, method = select_comparison_echo([0.012, 0.02974], 2, 30.0, 2)
        self.assertEqual((echo, method), (2, "explicit-echo"))

    def test_falls_back_to_echo_one_for_invalid_explicit_echo(self):
        echo, method = select_comparison_echo([0.012], 1, 30.0, 2)
        self.assertEqual((echo, method), (1, "explicit-echo-fallback-echo-1"))

    def test_requires_echo_times_for_automatic_selection(self):
        with self.assertRaisesRegex(ValueError, "no echo times"):
            select_comparison_echo([], 2, 30.0)

    def test_verifier_requires_map_for_selected_echo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            errors = comparison_tsnr_errors(
                {"GroupID": "sub-01_task-rest_run-1", "ComparisonBaseline": {"UsedEcho": 2}},
                Path(temp_dir),
            )
        self.assertIn("selected echo-2 is missing", errors[0])

    def test_verifier_checks_selected_echo_sidecar_and_source(self):
        group_id = "sub-01_task-rest_run-1"
        with tempfile.TemporaryDirectory() as temp_dir:
            tsnr_dir = Path(temp_dir)
            map_path = tsnr_dir / f"{group_id}_echo-2_desc-tsnr_bold.nii.gz"
            map_path.touch()
            sidecar = map_path.with_suffix("").with_suffix(".json")
            sidecar.write_text(json.dumps({
                "SourceImage": f"{group_id}_echo-2_desc-preproc_bold.nii.gz",
                "ComparisonBaseline": {"UsedEcho": 2},
            }))
            errors = comparison_tsnr_errors(
                {"GroupID": group_id, "ComparisonBaseline": {"UsedEcho": 2}},
                tsnr_dir,
            )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
