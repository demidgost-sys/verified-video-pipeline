"""Versioned public contracts. Changing semantics requires a new version."""

from __future__ import annotations

import math
from typing import Any

from verified_video_pipeline.errors import ContractError


PROFILE = "h264-aac-web"
RENDER_CONTRACT_VERSION = 2
LOUDNESS_PROFILE = {
    "id": "synthetic-web-loudness",
    "version": 1,
    "target_lufs": -16.0,
    "tolerance_lu": 1.0,
    "maximum_true_peak_dbtp": -1.0,
    "dual_mono": False,
}
# Explicit output sample rate is essential: loudnorm can upsample to 192 kHz.
LOUDNESS_FILTER = "loudnorm=I=-16:TP=-1:LRA=7:dual_mono=false"
LOUDNESS_METHOD = "ffmpeg-loudnorm-input-v1"


def is_loudness_profile(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value == LOUDNESS_PROFILE
        and type(value.get("version")) is int
        and type(value.get("dual_mono")) is bool
        and all(
            type(value.get(k)) in (int, float)
            for k in ("target_lufs", "tolerance_lu", "maximum_true_peak_dbtp")
        )
    )


def validate_loudness(evidence: Any) -> None:
    fields = {
        "profile",
        "method",
        "status",
        "integrated_lufs",
        "true_peak_dbtp",
        "range_lu",
        "threshold_lufs",
    }
    if not isinstance(evidence, dict) or set(evidence) != fields:
        raise ContractError("invalid loudness evidence shape")
    if (
        not is_loudness_profile(evidence["profile"])
        or evidence["method"] != LOUDNESS_METHOD
        or evidence["status"] != "PASS"
    ):
        raise ContractError("unsupported loudness evidence contract")
    for field in ("integrated_lufs", "true_peak_dbtp", "range_lu", "threshold_lufs"):
        value = evidence[field]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ContractError("loudness measurement must be finite")
    if (
        abs(evidence["integrated_lufs"] - LOUDNESS_PROFILE["target_lufs"])
        > LOUDNESS_PROFILE["tolerance_lu"]
        or evidence["true_peak_dbtp"] > LOUDNESS_PROFILE["maximum_true_peak_dbtp"]
        or not 0 <= evidence["range_lu"] <= 99
        or not -99 <= evidence["threshold_lufs"] <= 0
    ):
        raise ContractError("measured loudness is outside profile tolerance")
