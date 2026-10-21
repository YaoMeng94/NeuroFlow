import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

from pipelines.nextflow.run_pipeline_task import _bind_bids_input, _run_pipeline


class PipelineTaskLogTests(unittest.TestCase):
    def test_bids_input_must_match_tracked_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "bids"
            source.mkdir()
            command = ["bash", "run_bold_bidsapp.sh", str(source), str(root / "out")]

            _bind_bids_input(command, source)
            self.assertEqual(command[2], str(source.resolve()))

            with self.assertRaisesRegex(ValueError, "does not match"):
                _bind_bids_input(command, root)

    def test_child_output_is_forwarded_and_written_to_live_log(self):
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "pipeline.log"
            env = os.environ.copy()
            env["NEUROFLOW_TASK_LOG"] = str(log_path)
            command = [sys.executable, "-u", "-c", "print('first'); print('second')"]
            terminal = io.StringIO()
            with contextlib.redirect_stdout(terminal):
                return_code = _run_pipeline(command, env)

            self.assertEqual(return_code, 0)
            self.assertEqual(terminal.getvalue(), "first\nsecond\n")
            self.assertEqual(log_path.read_text(), "first\nsecond\n")


if __name__ == "__main__":
    unittest.main()
