"""Versioned, deliberately small release-assurance state machine."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from verified_video_pipeline.errors import ContractError


SCHEMA_VERSION = 1
CONTENT_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class Stage(StrEnum):
    REGISTERED = "REGISTERED"
    PLAN_APPROVED = "PLAN_APPROVED"
    MASTER_READY = "MASTER_READY"
    QA_PASSED = "QA_PASSED"
    READY = "READY"


NEXT_STAGE = {
    Stage.REGISTERED: Stage.PLAN_APPROVED,
    Stage.PLAN_APPROVED: Stage.MASTER_READY,
    Stage.MASTER_READY: Stage.QA_PASSED,
    Stage.QA_PASSED: Stage.READY,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_content_id(value: str) -> str:
    if not isinstance(value, str) or not CONTENT_ID.fullmatch(value):
        raise ContractError(
            "content id must be 1-64 lowercase ASCII letters, digits, or hyphens"
        )
    return value


def new_state(content_id: str, source: dict[str, Any]) -> dict[str, Any]:
    validate_content_id(content_id)
    state = {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "content_id": content_id,
        "stage": Stage.REGISTERED.value,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "source": deepcopy(source),
    }
    validate_state(state)
    return state


def validate_state(state: dict[str, Any]) -> None:
    if (
        type(state.get("schema_version")) is not int
        or state["schema_version"] != SCHEMA_VERSION
    ):
        raise ContractError("unsupported project schema version")
    if type(state.get("revision")) is not int or state["revision"] < 0:
        raise ContractError("project revision must be a non-negative integer")
    validate_content_id(state.get("content_id", ""))
    try:
        stage = Stage(state.get("stage"))
    except ValueError as exc:
        raise ContractError("unknown project stage") from exc

    source = state.get("source")
    _validate_artifact(source, "source")
    _validate_probe(source.get("probe"), "source")
    if stage is not Stage.REGISTERED:
        plan = state.get("approved_plan")
        _validate_artifact(plan, "approved plan")
        if not isinstance(plan.get("reviewer"), str) or not plan["reviewer"].strip():
            raise ContractError("approved plan requires a reviewer")
    if stage in {Stage.MASTER_READY, Stage.QA_PASSED, Stage.READY}:
        master = state.get("master")
        _validate_artifact(master, "master")
        if not isinstance(master.get("profile"), str) or not master["profile"].strip():
            raise ContractError("master profile is missing")
        _validate_probe(master.get("probe"), "master")
        _validate_binding(master, "source_sha256", source["sha256"], "source")
        _validate_binding(master, "plan_sha256", plan["sha256"], "approved plan")
    if stage in {Stage.QA_PASSED, Stage.READY}:
        qa = state.get("qa")
        if not isinstance(qa, dict) or qa.get("status") != "PASS":
            raise ContractError("QA evidence is missing or not PASS")
        checks = qa.get("checks")
        if (
            not isinstance(checks, list)
            or not checks
            or any(not isinstance(check, str) or not check.strip() for check in checks)
        ):
            raise ContractError("QA evidence has no checks")
        if not isinstance(qa.get("checked_at"), str) or not qa["checked_at"].strip():
            raise ContractError("QA evidence has no check time")
        _validate_probe(qa.get("probe"), "QA")
    if stage is Stage.READY:
        _validate_artifact(state.get("release_manifest"), "release manifest")


def _validate_artifact(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{label} identity is missing")
    path = value.get("path")
    sha256 = value.get("sha256")
    size = value.get("size")
    if not isinstance(path, str) or not path or path.startswith(("/", "..")):
        raise ContractError(f"{label} path must be a safe workspace-relative path")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ContractError(f"{label} SHA-256 is invalid")
    if type(size) is not int or size < 0:
        raise ContractError(f"{label} size is invalid")


def _validate_probe(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{label} probe is missing")
    duration = value.get("duration_seconds")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ContractError(f"{label} probe duration must be a finite positive number")


def _validate_binding(
    artifact: dict[str, Any], field: str, expected: str, label: str
) -> None:
    value = artifact.get(field)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError(f"master {label} binding is invalid")
    if value != expected:
        raise ContractError(f"master belongs to a different {label}")


def advance(state: dict[str, Any], target: Stage) -> dict[str, Any]:
    validate_state(state)
    current = Stage(state["stage"])
    if NEXT_STAGE.get(current) is not target:
        raise ContractError(f"illegal transition: {current.value} -> {target.value}")
    result = deepcopy(state)
    result["stage"] = target.value
    result["updated_at"] = utc_now()
    return result


def validate_transition(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Reject any journal transition that bypasses the public lifecycle."""

    validate_state(before)
    validate_state(after)
    current = Stage(before["stage"])
    target = Stage(after["stage"])
    if NEXT_STAGE.get(current) is not target:
        raise ContractError(
            f"illegal journal transition: {current.value} -> {target.value}"
        )
    for key in ("schema_version", "content_id", "created_at", "source"):
        if before.get(key) != after.get(key):
            raise ContractError(f"immutable project field changed: {key}")
    for key in ("approved_plan", "master", "qa", "release_manifest"):
        if key in before and before[key] != after.get(key):
            raise ContractError(f"downstream evidence changed during transition: {key}")
