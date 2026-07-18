from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from verified_video_pipeline.cli import main


class CliTests(unittest.TestCase):
    def test_missing_project_is_a_bounded_expected_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "private-parent" / "missing-project"
            error = io.StringIO()
            with redirect_stderr(error):
                result = main(["status", str(missing)])
            self.assertEqual(result, 2)
            self.assertIn("required file not found: project.json", error.getvalue())
            self.assertNotIn(str(missing.parent), error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

    def test_invalid_media_error_does_not_expose_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "private-customer" / "secret-project"
            project.mkdir(parents=True)
            source = project / "confidential-recording.mp4"
            source.write_text("not a media file", encoding="utf-8")
            error = io.StringIO()
            with redirect_stderr(error):
                result = main(
                    [
                        "init",
                        str(project),
                        str(source),
                        "--content-id",
                        "synthetic-fixture",
                    ]
                )
            message = error.getvalue()
            self.assertEqual(result, 2)
            self.assertIn("ffprobe failed with exit code", message)
            self.assertNotIn(str(project), message)
            self.assertNotIn(str(source), message)
            self.assertNotIn("confidential-recording", message)
            self.assertNotIn("Traceback", message)

    def test_incomplete_state_is_a_bounded_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "private-state"
            project.mkdir()
            state = {
                "schema_version": 1,
                "revision": 0,
                "content_id": "synthetic-fixture",
                "stage": "REGISTERED",
                "created_at": "2026-07-18T00:00:00+00:00",
                "updated_at": "2026-07-18T00:00:00+00:00",
                "source": {
                    "path": "source.mp4",
                    "sha256": "a" * 64,
                    "size": 4,
                },
            }
            (project / "project.json").write_text(json.dumps(state), encoding="utf-8")
            plan = project / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile": "h264-aac-web",
                        "trim": {"start_seconds": 0.0, "end_seconds": 1.0},
                    }
                ),
                encoding="utf-8",
            )
            error = io.StringIO()
            with redirect_stderr(error):
                result = main(
                    [
                        "approve-plan",
                        str(project),
                        str(plan),
                        "--reviewer",
                        "synthetic-reviewer",
                    ]
                )
            message = error.getvalue()
            self.assertEqual(result, 2)
            self.assertIn("source probe is missing", message)
            self.assertNotIn(str(project), message)
            self.assertNotIn("Traceback", message)


if __name__ == "__main__":
    unittest.main()
