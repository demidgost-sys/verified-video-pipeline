from __future__ import annotations

import json
import subprocess
import traceback
import unittest
from pathlib import Path
from unittest import mock

from verified_video_pipeline.errors import PipelineError
from verified_video_pipeline.loudness import measure_loudness, parse_loudness


def output(**changes):
    return json.dumps(
        {
            "input_i": "-16.0",
            "input_tp": "-2.0",
            "input_lra": "0.0",
            "input_thresh": "-26.0",
            **changes,
        }
    )


class LoudnessTests(unittest.TestCase):
    def test_inclusive_target_tolerance_and_true_peak_ceiling(self):
        for value in ("-17", "-16", "-15"):
            evidence = parse_loudness(output(input_i=value, input_tp="-1"))
            self.assertEqual(evidence["status"], "PASS")
        for changes in (
            {"input_i": "-17.001"},
            {"input_i": "-14.999"},
            {"input_tp": "-0.999"},
        ):
            with self.assertRaisesRegex(PipelineError, "tolerance"):
                parse_loudness(output(**changes))

    def test_malformed_silence_nonfinite_and_ambiguous_output_fail_closed(self):
        for value in (
            "",
            "{}",
            "[]",
            "not json",
            output(input_i="-inf"),
            output(input_tp="nan"),
            output(input_i=True),
            output(input_lra=-1),
            output() + output(),
            output(input_i="bad"),
            '{"output_i":"-16", "output_tp":"-2"}',
        ):
            with self.subTest(value=value), self.assertRaises(PipelineError):
                parse_loudness(value)

    def test_measures_input_with_argv_and_does_not_persist_diagnostics(self):
        private = Path("synthetic-private") / "input with spaces;anything.mp4"
        with (
            mock.patch(
                "verified_video_pipeline.loudness.require_binary", return_value="ffmpeg"
            ),
            mock.patch(
                "verified_video_pipeline.loudness._run",
                return_value=subprocess.CompletedProcess(
                    [], 0, "", "diagnostics " + str(private) + output()
                ),
            ) as run,
        ):
            evidence = measure_loudness(private)
        self.assertNotIn(str(private), json.dumps(evidence))
        self.assertIn(str(private), run.call_args.args[0])
        self.assertIn("0:a:0", run.call_args.args[0])

    def test_timeout_is_bounded_and_sanitized(self):
        with (
            mock.patch(
                "verified_video_pipeline.loudness.require_binary", return_value="ffmpeg"
            ),
            mock.patch(
                "verified_video_pipeline.media.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    ["ffmpeg", "synthetic-private.mp4"], 120
                ),
            ) as run,
        ):
            with self.assertRaises(PipelineError) as raised:
                measure_loudness(Path("synthetic-private.mp4"))
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        self.assertNotIn(
            "synthetic-private.mp4",
            "".join(traceback.format_exception(raised.exception)),
        )
