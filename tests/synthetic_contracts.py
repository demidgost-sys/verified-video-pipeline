"""Small public synthetic evidence for state/model tests; contains no media."""

from copy import deepcopy

from verified_video_pipeline.atomic import json_sha256
from verified_video_pipeline.engine import LIBRARIES
from verified_video_pipeline.profiles import (
    LOUDNESS_METHOD,
    LOUDNESS_PROFILE,
    PROFILE,
    RENDER_CONTRACT_VERSION,
)


def engine_record():
    required = {
        "ffmpeg": {"version": "7.0", "libraries": dict.fromkeys(LIBRARIES, "1.2.3")},
        "ffprobe": {"version": "7.0", "libraries": dict.fromkeys(LIBRARIES, "1.2.3")},
        "profile": PROFILE,
        "render_contract_version": RENDER_CONTRACT_VERSION,
        "loudness_profile": deepcopy(LOUDNESS_PROFILE),
        "canary_sha256": "e" * 64,
    }
    return {
        "schema_version": 1,
        "required": required,
        "sha256": json_sha256(required),
        "informational": {
            "platform": "Linux",
            "build_ids": dict.fromkeys(("ffmpeg", "ffprobe"), "f" * 64),
        },
    }


def loudness_record():
    return {
        "profile": deepcopy(LOUDNESS_PROFILE),
        "method": LOUDNESS_METHOD,
        "status": "PASS",
        "integrated_lufs": -16.0,
        "true_peak_dbtp": -3.0,
        "range_lu": 0.0,
        "threshold_lufs": -26.0,
    }
