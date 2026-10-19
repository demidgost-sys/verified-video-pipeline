from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from copy import deepcopy
from unittest import mock

from synthetic_contracts import engine_record
from verified_video_pipeline.atomic import json_sha256
from verified_video_pipeline.engine import (
    LIBRARIES,
    capture_engine,
    normalize_version,
    require_engine,
    validate_engine,
)
from verified_video_pipeline.errors import ContractError, IntegrityError, PipelineError


def version_output(name, suffix=""):
    return f"{name} version 7.0{suffix}\n" + "\n".join(
        f"{library} 1. 2. 3 / 1. 2. 3" for library in LIBRARIES
    )


class EngineContractTests(unittest.TestCase):
    def test_linux_and_macos_metadata_is_informational_and_sanitized(self):
        records = []
        for system, suffix in (("Linux", "-distribution"), ("Darwin", "_build")):

            def run(argv):
                if argv[-1] == "-version":
                    output = (
                        version_output(argv[0], suffix)
                        + "\nconfiguration: --prefix=/synthetic/build\n"
                    )
                else:
                    output = "SHA256=" + "a" * 64 + "\n"
                return subprocess.CompletedProcess(argv, 0, output, "")

            with (
                mock.patch("verified_video_pipeline.engine._run", side_effect=run),
                mock.patch(
                    "verified_video_pipeline.engine.require_binary",
                    side_effect=lambda name: name,
                ),
                mock.patch(
                    "verified_video_pipeline.engine.platform.system",
                    return_value=system,
                ),
            ):
                records.append(capture_engine())
        require_engine(*records)
        self.assertNotEqual(records[0]["informational"], records[1]["informational"])
        self.assertNotIn("configuration", json.dumps(records))
        self.assertNotIn("/synthetic/build", json.dumps(records))

    def test_versions_and_codec_behavior_invalidate_prior_build(self):
        original = engine_record()
        for field in ("version", "library", "canary"):
            changed = deepcopy(original)
            if field == "version":
                changed["required"]["ffmpeg"]["version"] = "8.0"
            elif field == "library":
                changed["required"]["ffprobe"]["libraries"]["libavformat"] = "9.1.2"
            else:
                changed["required"]["canary_sha256"] = "0" * 64
            changed["sha256"] = json_sha256(changed["required"])
            with self.subTest(field=field), self.assertRaises(IntegrityError):
                require_engine(original, changed)

    def test_unrecognized_engine_and_forged_contract_fail_closed(self):
        for output in ("ffmpeg version N-development", "ffmpeg version 8.0"):
            with self.assertRaises(PipelineError):
                normalize_version("ffmpeg", output)
        record = engine_record()
        record["required"]["canary_sha256"] = "1" * 64
        with self.assertRaises(ContractError):
            validate_engine(record)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required"
    )
    def test_real_canary_is_deterministic(self):
        first = capture_engine()
        second = capture_engine()
        validate_engine(first)
        self.assertEqual(first, second)
