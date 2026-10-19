"""Measure input loudness of exact master bytes, never normalized output stats."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from verified_video_pipeline.errors import ContractError, PipelineError
from verified_video_pipeline.media import _run, require_binary
from verified_video_pipeline.profiles import (
    LOUDNESS_FILTER,
    LOUDNESS_METHOD,
    LOUDNESS_PROFILE,
    validate_loudness,
)


def parse_loudness(output: str) -> dict[str, Any]:
    # Diagnostics can contain paths. Never persist or chain them into errors.
    blocks = re.findall(r"\{[^{}]*\}", output)
    evidence = None
    if len(blocks) == 1:
        try:
            raw = json.loads(blocks[0])
            names = {
                "integrated_lufs": "input_i",
                "true_peak_dbtp": "input_tp",
                "range_lu": "input_lra",
                "threshold_lufs": "input_thresh",
            }
            numbers = {}
            for name, key in names.items():
                value = raw[key]
                if type(value) not in (str, float, int):
                    raise ValueError
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError
                numbers[name] = number
            evidence = {
                "profile": deepcopy(LOUDNESS_PROFILE),
                "method": LOUDNESS_METHOD,
                "status": "PASS",
                **numbers,
            }
        except (KeyError, TypeError, ValueError):
            pass
    if evidence is None:
        raise PipelineError("FFmpeg returned malformed loudness measurements")
    try:
        validate_loudness(evidence)
    except ContractError:
        evidence = None
    if evidence is None:
        raise PipelineError("measured loudness is outside profile tolerance")
    return evidence


def measure_loudness(path: Path) -> dict[str, Any]:
    result = _run(
        [
            require_binary("ffmpeg"),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            f"{LOUDNESS_FILTER}:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    return parse_loudness(result.stderr)
