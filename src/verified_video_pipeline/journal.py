"""Crash-recoverable compare-and-swap journal for project state."""

from __future__ import annotations

import os
import secrets
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from verified_video_pipeline.atomic import (
    atomic_unlink,
    atomic_write_json,
    json_sha256,
    read_json,
)
from verified_video_pipeline.errors import ContractError, RecoveryRequired
from verified_video_pipeline.state import utc_now, validate_state, validate_transition


Mutator = Callable[[dict[str, Any]], dict[str, Any]]


class StateStore:
    """Own a project state file and its single outstanding WAL record."""

    def __init__(self, root: Path):
        self.root = root
        self.state_path = root / "project.json"
        self.journal_path = root / "journal.json"

    def initialize(self, state: dict[str, Any]) -> None:
        if os.path.lexists(self.state_path) or os.path.lexists(self.journal_path):
            raise ContractError("project state already exists")
        validate_state(state)
        atomic_write_json(self.state_path, state, mode=0o600)

    def load(self) -> dict[str, Any]:
        state = read_json(self.state_path)
        validate_state(state)
        return state

    def has_pending_recovery(self) -> bool:
        return os.path.lexists(self.journal_path)

    def transact(self, mutator: Mutator, *, fault: str | None = None) -> dict[str, Any]:
        if os.path.lexists(self.journal_path):
            raise RecoveryRequired(
                "pending journal must be recovered before a new transition"
            )
        before = self.load()
        after = mutator(deepcopy(before))
        if not isinstance(after, dict):
            raise ContractError("state mutator must return a JSON object")
        after["revision"] = before["revision"] + 1
        validate_state(after)
        validate_transition(before, after)

        record = {
            "schema_version": 1,
            "transaction_id": secrets.token_hex(12),
            "created_at": utc_now(),
            "before_sha256": json_sha256(before),
            "after_sha256": json_sha256(after),
            "before_state": before,
            "after_state": after,
        }
        record["record_sha256"] = json_sha256(record)
        atomic_write_json(self.journal_path, record, mode=0o600)
        if fault == "after_prepare":
            raise RuntimeError("injected crash after journal prepare")

        current = self.load()
        if json_sha256(current) != record["before_sha256"]:
            raise RecoveryRequired(
                "state changed outside the journal; operator review required"
            )
        atomic_write_json(self.state_path, after, mode=0o600)
        if fault == "after_apply":
            raise RuntimeError("injected crash after state apply")
        atomic_unlink(self.journal_path)
        return after

    def recover(self) -> bool:
        if not os.path.lexists(self.journal_path):
            return False
        record = read_json(self.journal_path)
        claimed_hash = record.pop("record_sha256", None)
        if claimed_hash != json_sha256(record):
            raise RecoveryRequired("journal self-hash mismatch")
        if (
            type(record.get("schema_version")) is not int
            or record["schema_version"] != 1
        ):
            raise RecoveryRequired("unsupported journal schema version")
        target = record.get("after_state")
        before = record.get("before_state")
        if not isinstance(before, dict) or not isinstance(target, dict):
            raise RecoveryRequired("journal before/after state is missing")
        validate_state(before)
        validate_state(target)
        validate_transition(before, target)
        if target["revision"] != before["revision"] + 1:
            raise RecoveryRequired("journal target revision is not the next revision")
        if json_sha256(before) != record.get("before_sha256"):
            raise RecoveryRequired("journal before-state hash mismatch")
        if json_sha256(target) != record.get("after_sha256"):
            raise RecoveryRequired("journal target hash mismatch")

        current = self.load()
        current_hash = json_sha256(current)
        if current_hash == record.get("before_sha256"):
            atomic_write_json(self.state_path, target, mode=0o600)
        elif current_hash != record.get("after_sha256"):
            raise RecoveryRequired(
                "state matches neither side of the pending transaction"
            )
        atomic_unlink(self.journal_path)
        return True
