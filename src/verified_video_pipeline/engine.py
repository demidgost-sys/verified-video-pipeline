"""Privacy-safe FFmpeg contract with a deterministic synthetic codec canary."""

from __future__ import annotations

import hashlib
import platform
import re
from copy import deepcopy
from typing import Any

from verified_video_pipeline.atomic import json_sha256
from verified_video_pipeline.errors import ContractError, IntegrityError, PipelineError
from verified_video_pipeline.media import _run, require_binary
from verified_video_pipeline.profiles import (
    LOUDNESS_FILTER,
    LOUDNESS_PROFILE,
    PROFILE,
    RENDER_CONTRACT_VERSION,
    is_loudness_profile,
)


LIBRARIES = (
    "libavutil",
    "libavcodec",
    "libavformat",
    "libavfilter",
    "libswscale",
    "libswresample",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def normalize_version(name: str, output: str) -> dict[str, Any]:
    version = re.search(rf"^{name} version (\d+\.\d+(?:\.\d+)?)(?=$|[\s+_-])", output)
    libraries = {}
    for library in LIBRARIES:
        match = re.search(
            rf"^{library}\s+(\d+)\.\s*(\d+)\.\s*(\d+)\s*/\s*(\d+)\.\s*(\d+)\.\s*(\d+)",
            output,
            re.MULTILINE,
        )
        if match:
            # Record linked runtime versions, not just compiled header versions.
            libraries[library] = ".".join(str(int(x)) for x in match.groups()[3:])
        elif name == "ffmpeg" or library != "libavfilter":
            raise PipelineError(
                "FFmpeg engine contract has incomplete library versions"
            )
    if version is None:
        raise PipelineError("FFmpeg engine contract requires a stable numeric version")
    return {"version": version[1], "libraries": libraries}


def capture_engine() -> dict[str, Any]:
    """Caller holds the heavy-media lease. No machine paths or raw logs escape."""
    tools = {}
    build_ids = {}
    for name in ("ffmpeg", "ffprobe"):
        output = _run([require_binary(name), "-version"]).stdout
        tools[name] = normalize_version(name, output)
        build_ids[name] = hashlib.sha256(output.encode("utf-8")).hexdigest()
    # Fixed inputs, single-threaded software encoding, and disabled SIMD keep
    # the canary independent of scheduler order and CPU feature selection.
    result = _run(
        [
            require_binary("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-cpuflags",
            "0",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x32:rate=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000",
            "-t",
            "0.5",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-threads:v",
            "1",
            "-x264-params",
            "asm=0:threads=1",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-af",
            LOUDNESS_FILTER,
            "-ar",
            "48000",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ]
    )
    match = re.fullmatch(r"SHA256=([0-9a-f]{64})\s*", result.stdout)
    if match is None:
        raise PipelineError("FFmpeg engine canary returned an invalid digest")
    required = {
        **tools,
        "profile": PROFILE,
        "render_contract_version": RENDER_CONTRACT_VERSION,
        "loudness_profile": deepcopy(LOUDNESS_PROFILE),
        "canary_sha256": match[1],
    }
    return {
        "schema_version": 1,
        "required": required,
        "sha256": json_sha256(required),
        "informational": {"platform": platform.system(), "build_ids": build_ids},
    }


def validate_engine(record: Any) -> None:
    if not isinstance(record, dict) or set(record) != {
        "schema_version",
        "required",
        "sha256",
        "informational",
    }:
        raise ContractError("missing or malformed engine contract; rebuild required")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ContractError("unsupported engine contract version")
    required = record["required"]
    if not isinstance(required, dict) or set(required) != {
        "ffmpeg",
        "ffprobe",
        "profile",
        "render_contract_version",
        "loudness_profile",
        "canary_sha256",
    }:
        raise ContractError("invalid engine requirements")
    if (
        required["profile"] != PROFILE
        or type(required["render_contract_version"]) is not int
        or required["render_contract_version"] != RENDER_CONTRACT_VERSION
        or not is_loudness_profile(required["loudness_profile"])
        or not isinstance(required["canary_sha256"], str)
        or not SHA256.fullmatch(required["canary_sha256"])
        or record["sha256"] != json_sha256(required)
    ):
        raise ContractError("invalid engine contract binding")
    for name in ("ffmpeg", "ffprobe"):
        tool = required[name]
        if not isinstance(tool, dict) or set(tool) != {"version", "libraries"}:
            raise ContractError("invalid engine tool contract")
        if not isinstance(tool["version"], str) or not re.fullmatch(
            r"\d+\.\d+(?:\.\d+)?", tool["version"]
        ):
            raise ContractError("invalid engine tool version")
        libraries = tool["libraries"]
        expected = set(LIBRARIES) - ({"libavfilter"} if name == "ffprobe" else set())
        if not isinstance(libraries, dict) or not expected <= set(libraries) <= set(
            LIBRARIES
        ):
            raise ContractError("invalid engine libraries")
        if any(
            not isinstance(v, str) or not re.fullmatch(r"\d+\.\d+\.\d+", v)
            for v in libraries.values()
        ):
            raise ContractError("invalid engine library version")
    info = record["informational"]
    if (
        not isinstance(info, dict)
        or set(info) != {"platform", "build_ids"}
        or info["platform"] not in {"Linux", "Darwin"}
        or not isinstance(info["build_ids"], dict)
        or set(info["build_ids"]) != {"ffmpeg", "ffprobe"}
        or any(
            not isinstance(v, str) or not SHA256.fullmatch(v)
            for v in info["build_ids"].values()
        )
    ):
        raise ContractError("invalid engine informational metadata")


def require_engine(expected: Any, actual: dict[str, Any]) -> None:
    validate_engine(expected)
    validate_engine(actual)
    if expected["sha256"] != actual["sha256"]:
        raise IntegrityError(
            "FFmpeg engine contract drift; rebuild and human approval required"
        )
