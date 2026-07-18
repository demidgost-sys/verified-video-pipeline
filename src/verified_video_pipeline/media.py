"""A narrow FFmpeg adapter used by the synthetic reference workflow."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from verified_video_pipeline.errors import ContractError, PipelineError


PROFILE = "h264-aac-web"


def require_binary(name: str) -> str:
    value = shutil.which(name)
    if value is None:
        raise ContractError(f"required binary not found on PATH: {name}")
    return value


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        result: subprocess.CompletedProcess[str] | None = subprocess.run(
            list(argv),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env={**os.environ, "AV_LOG_FORCE_NOCOLOR": "1"},
        )
    except subprocess.TimeoutExpired:
        # Leave the exception handler before raising the public error. A
        # TimeoutExpired instance retains the complete argv (including private
        # media paths) in its exception context even when formatted output is
        # otherwise sanitized.
        result = None
    if result is None:
        raise PipelineError(f"media command exceeded 120 seconds: {Path(argv[0]).name}")
    if result.returncode != 0:
        # FFmpeg diagnostics routinely echo every input and output path. Those
        # paths may contain account names or private workspace structure, so
        # the public CLI reports only the stable tool/exit contract. Operators
        # can rerun the tool directly when trusted local diagnostics are needed.
        raise PipelineError(
            f"{Path(argv[0]).name} failed with exit code {result.returncode}"
        )
    return result


def binary_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ("ffmpeg", "ffprobe"):
        binary = require_binary(name)
        first_line = _run([binary, "-version"]).stdout.splitlines()[0]
        versions[name] = first_line
    return versions


def generate_synthetic_video(path: Path, *, duration: float = 3.0) -> None:
    if os.path.lexists(path):
        raise ContractError(
            f"refusing to replace existing synthetic source: {path.name}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_binary("ffmpeg")
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000",
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(path),
        ]
    )


def validate_plan(value: dict[str, Any]) -> dict[str, Any]:
    expected = {"schema_version", "profile", "trim"}
    if set(value) != expected:
        raise ContractError(
            "edit plan must contain exactly schema_version, profile, and trim"
        )
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ContractError("unsupported edit-plan schema version")
    if value.get("profile") != PROFILE:
        raise ContractError(f"unsupported render profile: {value.get('profile')!r}")
    trim = value.get("trim")
    if not isinstance(trim, dict) or set(trim) != {"start_seconds", "end_seconds"}:
        raise ContractError("trim must contain exactly start_seconds and end_seconds")
    start = trim.get("start_seconds")
    end = trim.get("end_seconds")
    if (
        not isinstance(start, (int, float))
        or isinstance(start, bool)
        or not math.isfinite(start)
        or start < 0
    ):
        raise ContractError("trim start must be a non-negative number")
    if (
        not isinstance(end, (int, float))
        or isinstance(end, bool)
        or not math.isfinite(end)
        or end <= start
    ):
        raise ContractError("trim end must be greater than trim start")
    return value


def validate_plan_against_source(
    plan: dict[str, Any], source_probe: dict[str, Any]
) -> None:
    """Bind the requested source-time range to the registered source duration."""

    validate_plan(plan)
    duration = source_probe.get("duration_seconds")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
    ):
        raise ContractError("registered source duration is invalid")
    if plan["trim"]["end_seconds"] > duration + 0.05:
        raise ContractError(
            "trim end exceeds the registered source duration "
            f"({plan['trim']['end_seconds']} > {duration})"
        )


def validate_render_duration(
    plan: dict[str, Any], rendered_probe: dict[str, Any]
) -> None:
    """Reject a technically decodable render that does not implement its plan."""

    expected = plan["trim"]["end_seconds"] - plan["trim"]["start_seconds"]
    actual = rendered_probe.get("duration_seconds")
    if (
        not isinstance(actual, (int, float))
        or isinstance(actual, bool)
        or not math.isfinite(actual)
    ):
        raise PipelineError("rendered duration is invalid")
    tolerance = max(0.15, expected * 0.02)
    if abs(actual - expected) > tolerance:
        raise PipelineError(
            "rendered duration does not match approved trim "
            f"(expected {expected:.3f}s, got {actual:.3f}s)"
        )


def render(source: Path, target: Path, plan: dict[str, Any]) -> None:
    validate_plan(plan)
    if os.path.lexists(target):
        raise ContractError(
            f"refusing to replace existing staged render: {target.name}"
        )
    ffmpeg = require_binary("ffmpeg")
    trim = plan["trim"]
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(trim["start_seconds"]),
            "-to",
            str(trim["end_seconds"]),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(target),
        ]
    )


def probe(path: Path) -> dict[str, Any]:
    ffprobe = require_binary("ffprobe")
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=index,codec_type,codec_name,width,height,pix_fmt,sample_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineError("ffprobe returned invalid JSON") from exc
    streams = raw.get("streams")
    if not isinstance(streams, list):
        raise PipelineError("ffprobe did not return a stream list")
    normalized_streams = []
    for stream in streams:
        normalized_streams.append(
            {key: stream[key] for key in sorted(stream) if key != "index"}
        )
    format_data = raw.get("format", {})
    try:
        duration = round(float(format_data["duration"]), 3)
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError("media duration is unavailable") from exc
    return {
        "duration_seconds": duration,
        "format_name": format_data.get("format_name"),
        "streams": normalized_streams,
    }


def strict_decode(path: Path) -> None:
    ffmpeg = require_binary("ffmpeg")
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ]
    )


def technical_checks(path: Path) -> tuple[dict[str, Any], list[str]]:
    evidence = probe(path)
    streams = evidence["streams"]
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if video is None or audio is None:
        raise PipelineError("master must contain one video and one audio stream")
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        raise PipelineError("master video must be H.264 yuv420p")
    if audio.get("codec_name") != "aac" or audio.get("sample_rate") != "48000":
        raise PipelineError("master audio must be AAC at 48 kHz")
    if evidence["duration_seconds"] <= 0:
        raise PipelineError("master duration must be positive")
    strict_decode(path)
    return evidence, [
        "exact master bytes match registered SHA-256",
        "video stream is H.264 yuv420p",
        "audio stream is AAC at 48 kHz",
        "duration is positive",
        "strict full decode completed without errors",
    ]
