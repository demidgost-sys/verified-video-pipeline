from __future__ import annotations

import math
import subprocess
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

from verified_video_pipeline.errors import ContractError, PipelineError
from verified_video_pipeline.media import PROFILE, _run, validate_plan


class MediaContractTests(unittest.TestCase):
    def test_plan_is_strict_and_has_no_raw_ffmpeg_arguments(self) -> None:
        plan = {
            "schema_version": 1,
            "profile": PROFILE,
            "trim": {"start_seconds": 0.1, "end_seconds": 1.0},
        }
        self.assertIs(validate_plan(plan), plan)
        with self.assertRaises(ContractError):
            validate_plan({**plan, "ffmpeg_args": ["-anything"]})

    def test_trim_must_move_forward(self) -> None:
        with self.assertRaises(ContractError):
            validate_plan(
                {
                    "schema_version": 1,
                    "profile": PROFILE,
                    "trim": {"start_seconds": 2, "end_seconds": 1},
                }
            )

    def test_boolean_schema_version_is_not_integer_one(self) -> None:
        with self.assertRaises(ContractError):
            validate_plan(
                {
                    "schema_version": True,
                    "profile": PROFILE,
                    "trim": {"start_seconds": 0, "end_seconds": 1},
                }
            )

    def test_trim_numbers_must_be_finite(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_plan(
                    {
                        "schema_version": 1,
                        "profile": PROFILE,
                        "trim": {"start_seconds": 0, "end_seconds": value},
                    }
                )

    def test_timeout_error_has_no_private_argv_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private_path = Path(directory) / "private-customer" / "recording.mp4"
            timeout = subprocess.TimeoutExpired(
                cmd=["ffprobe", str(private_path)], timeout=120
            )
            with mock.patch(
                "verified_video_pipeline.media.subprocess.run", side_effect=timeout
            ):
                with self.assertRaises(PipelineError) as raised:
                    _run(["ffprobe", str(private_path)])

            error = raised.exception
            formatted = "".join(traceback.format_exception(error))
            self.assertIsNone(error.__context__)
            self.assertNotIn(str(private_path), formatted)
            self.assertNotIn("recording.mp4", formatted)
            self.assertIn("ffprobe", str(error))


if __name__ == "__main__":
    unittest.main()
