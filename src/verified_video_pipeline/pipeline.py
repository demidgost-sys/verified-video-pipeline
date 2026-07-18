"""High-level release-assurance operations for one local video project."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from verified_video_pipeline import __version__
from verified_video_pipeline.atomic import (
    atomic_unlink,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    fsync_directory,
    json_sha256,
    parse_json_object,
    read_json,
)
from verified_video_pipeline.errors import (
    ContractError,
    IntegrityError,
    RecoveryRequired,
)
from verified_video_pipeline.hashing import (
    require_identity,
    stable_read_bytes,
    stable_sha256_file,
)
from verified_video_pipeline.journal import StateStore
from verified_video_pipeline.locks import heavy_media_lease, project_lease
from verified_video_pipeline.media import (
    PROFILE,
    generate_synthetic_video,
    probe,
    render,
    technical_checks,
    validate_plan,
    validate_plan_against_source,
    validate_render_duration,
)
from verified_video_pipeline.state import Stage, advance, new_state, utc_now


SOURCE_NAME = "source.mp4"
PLAN_NAME = "approved-plan.json"
MASTER_NAME = "master.mp4"
STAGING_NAME = ".master.staged.mp4"
BUILD_RECEIPT_NAME = "build-receipt.json"
MANIFEST_NAME = "release-manifest.json"
MANAGED_ROOT_NAMES = {
    ".vvp.lock",
    "project.json",
    "journal.json",
    PLAN_NAME,
    MASTER_NAME,
    STAGING_NAME,
    BUILD_RECEIPT_NAME,
    MANIFEST_NAME,
}


def _relative_path(root: Path, path: Path) -> str:
    root_resolved = root.resolve()
    root_lexical = root.absolute()
    path_lexical = path.absolute()
    try:
        lexical_relative = path_lexical.relative_to(root_lexical)
    except ValueError:
        lexical_relative = None
    if lexical_relative is not None:
        lexical_cursor = root_lexical
        for part in lexical_relative.parts:
            lexical_cursor /= part
            if lexical_cursor.is_symlink():
                raise ContractError("artifact path cannot traverse a symlink")
    path_resolved = path.resolve()
    try:
        relative = path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ContractError("artifacts must live inside the project workspace") from exc
    cursor = root_resolved
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ContractError("artifact path cannot traverse a symlink")
    if any(part == ".." for part in relative.parts):
        raise ContractError(
            "artifact path cannot contain a symlink or parent traversal"
        )
    return relative.as_posix()


def _artifact_path(root: Path, identity: dict[str, Any]) -> Path:
    raw = identity.get("path")
    if not isinstance(raw, str):
        raise ContractError("artifact path is missing")
    candidate = root / raw
    _relative_path(root, candidate)
    return candidate


def _probed_identity(root: Path, path: Path) -> dict[str, Any]:
    before = stable_sha256_file(path)
    media_probe = probe(path)
    after = stable_sha256_file(path)
    if before != after:
        raise IntegrityError(f"artifact changed while probing: {path.name}")
    return {
        "path": _relative_path(root, path),
        **after,
        "probe": media_probe,
    }


def _checked_media_identity(root: Path, path: Path) -> tuple[dict[str, Any], list[str]]:
    before = stable_sha256_file(path)
    media_probe, checks = technical_checks(path)
    after = stable_sha256_file(path)
    if before != after:
        raise IntegrityError(f"artifact changed during technical checks: {path.name}")
    return (
        {
            "path": _relative_path(root, path),
            **after,
            "probe": media_probe,
        },
        checks,
    )


def _read_bound_json(
    path: Path, expected: dict[str, Any], *, label: str
) -> dict[str, Any]:
    data, actual = stable_read_bytes(path)
    registered = {"sha256": expected.get("sha256"), "size": expected.get("size")}
    if actual != registered:
        raise IntegrityError(f"{label} bytes changed after approval: {path.name}")
    return parse_json_object(data, name=path.name)


def _validate_reviewer(reviewer: str) -> str:
    reviewer = reviewer.strip()
    if not reviewer or len(reviewer) > 80 or any(char in reviewer for char in "\r\n"):
        raise ContractError(
            "reviewer must be a single non-empty line of at most 80 characters"
        )
    return reviewer


def _preflight_new_workspace(root: Path, source: Path) -> None:
    relative = _relative_path(root, source)
    if len(Path(relative).parts) == 1 and relative in MANAGED_ROOT_NAMES:
        raise ContractError(f"source cannot use reserved runtime path: {relative}")
    for name in MANAGED_ROOT_NAMES - {".vvp.lock"}:
        managed = root / name
        if os.path.lexists(managed):
            raise ContractError(f"reserved runtime path already exists: {name}")
    lock_path = root / ".vvp.lock"
    if os.path.lexists(lock_path):
        try:
            if os.path.samefile(source, lock_path):
                raise ContractError("source aliases the project lease path")
        except FileNotFoundError:
            pass


def _write_exact_json(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    expected_bytes = canonical_json_bytes(value)
    if os.path.lexists(path):
        if path.is_symlink():
            raise ContractError(f"refusing evidence symlink: {path.name}")
        actual_bytes, identity = stable_read_bytes(path)
        if actual_bytes != expected_bytes:
            raise IntegrityError(
                f"refusing to replace different existing evidence: {path.name}"
            )
        return identity
    atomic_write_bytes(path, expected_bytes)
    actual_bytes, identity = stable_read_bytes(path)
    if actual_bytes != expected_bytes:
        raise IntegrityError(f"evidence changed immediately after write: {path.name}")
    return identity


def initialize(root: Path, source: Path, content_id: str) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    _preflight_new_workspace(root, source)
    with project_lease(root):
        source_data = _probed_identity(root, source)
        state = new_state(content_id, source_data)
        StateStore(root).initialize(state)
        return state


def approve_plan(root: Path, plan_path: Path, reviewer: str) -> dict[str, Any]:
    reviewer = _validate_reviewer(reviewer)
    with project_lease(root):
        store = StateStore(root)
        _recover_locked(root, store)
        state = store.load()
        if Stage(state["stage"]) is not Stage.REGISTERED:
            raise ContractError("plan approval requires REGISTERED stage")
        source = _artifact_path(root, state["source"])
        require_identity(source, state["source"], label="source")
        plan = validate_plan(read_json(plan_path))
        validate_plan_against_source(plan, state["source"]["probe"])
        approved_path = root / PLAN_NAME
        plan_bytes = _write_exact_json(approved_path, plan)
        plan_identity = {
            "path": _relative_path(root, approved_path),
            **plan_bytes,
            "reviewer": reviewer,
            "approved_at": utc_now(),
        }

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            result = advance(current, Stage.PLAN_APPROVED)
            result["approved_plan"] = plan_identity
            return result

        return store.transact(mutate)


def build(root: Path) -> dict[str, Any]:
    with project_lease(root), heavy_media_lease():
        store = StateStore(root)
        _recover_locked(root, store)
        state = store.load()
        if Stage(state["stage"]) is Stage.MASTER_READY:
            require_identity(
                _artifact_path(root, state["master"]), state["master"], label="master"
            )
            return state
        if Stage(state["stage"]) is not Stage.PLAN_APPROVED:
            raise ContractError("build requires PLAN_APPROVED stage")

        source = _artifact_path(root, state["source"])
        approved_plan = _artifact_path(root, state["approved_plan"])
        require_identity(source, state["source"], label="source")
        plan = validate_plan(
            _read_bound_json(
                approved_plan, state["approved_plan"], label="approved plan"
            )
        )
        validate_plan_against_source(plan, state["source"]["probe"])

        staged = root / STAGING_NAME
        receipt_path = root / BUILD_RECEIPT_NAME
        master = root / MASTER_NAME
        if os.path.lexists(receipt_path):
            return _recover_build_locked(root, store, state)
        if os.path.lexists(master):
            raise RecoveryRequired(
                "unregistered master exists; no-clobber policy requires review"
            )
        if os.path.lexists(staged):
            if staged.is_symlink():
                raise ContractError("refusing symlink at staged render path")
            raise RecoveryRequired(
                "unregistered staging entry exists; operator review required"
            )

        render(source, staged, plan)
        checked_master, _ = _checked_media_identity(root, staged)
        master_probe = checked_master["probe"]
        validate_render_duration(plan, master_probe)
        # The final path does not exist yet; bind staged bytes to its future name.
        master_identity = {
            "path": _relative_path(root, master),
            "sha256": checked_master["sha256"],
            "size": checked_master["size"],
            "probe": master_probe,
            "profile": PROFILE,
            "source_sha256": state["source"]["sha256"],
            "plan_sha256": state["approved_plan"]["sha256"],
            "built_at": utc_now(),
        }
        receipt = {
            "schema_version": 1,
            "source_sha256": state["source"]["sha256"],
            "plan_sha256": state["approved_plan"]["sha256"],
            "staged_path": STAGING_NAME,
            "master": master_identity,
            "created_at": utc_now(),
        }
        receipt["record_sha256"] = json_sha256(receipt)
        atomic_write_json(receipt_path, receipt, mode=0o600)
        _install_no_clobber(staged, master)
        return _commit_master_locked(root, store, state, receipt)


def _install_no_clobber(staged: Path, target: Path) -> None:
    try:
        os.link(staged, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise RecoveryRequired(
            f"refusing to replace existing artifact: {target.name}"
        ) from exc
    except OSError as exc:
        raise RecoveryRequired(
            "no-clobber promotion requires staging and target on one local filesystem"
        ) from exc
    fsync_directory(target.parent)
    staged.unlink()
    fsync_directory(target.parent)


def _validate_receipt(path: Path) -> dict[str, Any]:
    receipt = read_json(path)
    claimed = receipt.pop("record_sha256", None)
    if claimed != json_sha256(receipt):
        raise RecoveryRequired("build receipt self-hash mismatch")
    if type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1:
        raise RecoveryRequired("unsupported build receipt schema version")
    master = receipt.get("master")
    if not isinstance(master, dict):
        raise RecoveryRequired("build receipt has no master identity")
    return receipt


def _recover_build_locked(
    root: Path, store: StateStore, state: dict[str, Any]
) -> dict[str, Any]:
    receipt_path = root / BUILD_RECEIPT_NAME
    receipt = _validate_receipt(receipt_path)
    if receipt.get("source_sha256") != state["source"]["sha256"]:
        raise RecoveryRequired("build receipt belongs to a different source")
    if receipt.get("plan_sha256") != state["approved_plan"]["sha256"]:
        raise RecoveryRequired("build receipt belongs to a different approved plan")
    master_identity = receipt["master"]
    master = _artifact_path(root, master_identity)
    staged_path = receipt.get("staged_path")
    if staged_path != STAGING_NAME:
        raise RecoveryRequired("build receipt staging path is invalid")
    staged = root / staged_path
    if os.path.lexists(master):
        if master.is_symlink():
            raise RecoveryRequired("recovered master path is a symlink")
        require_identity(master, master_identity, label="recovered master")
        if os.path.lexists(staged):
            require_identity(staged, master_identity, label="recovered staging")
            staged.unlink()
            fsync_directory(root)
    elif os.path.lexists(staged):
        require_identity(staged, master_identity, label="recovered staging")
        _install_no_clobber(staged, master)
    else:
        raise RecoveryRequired(
            "build receipt exists but neither staged nor final bytes exist"
        )
    return _commit_master_locked(root, store, state, receipt)


def _commit_master_locked(
    root: Path,
    store: StateStore,
    state: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    master_identity = receipt["master"]
    master = _artifact_path(root, master_identity)
    require_identity(master, master_identity, label="master")

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        result = advance(current, Stage.MASTER_READY)
        result["master"] = master_identity
        return result

    result = store.transact(mutate)
    receipt_path = root / BUILD_RECEIPT_NAME
    if os.path.lexists(receipt_path):
        atomic_unlink(receipt_path)
    return result


def run_qa(root: Path) -> dict[str, Any]:
    with project_lease(root), heavy_media_lease():
        store = StateStore(root)
        _recover_locked(root, store)
        state = store.load()
        if Stage(state["stage"]) is not Stage.MASTER_READY:
            raise ContractError("QA requires MASTER_READY stage")
        source = _artifact_path(root, state["source"])
        plan = _artifact_path(root, state["approved_plan"])
        master = _artifact_path(root, state["master"])
        require_identity(source, state["source"], label="source")
        approved_plan = validate_plan(
            _read_bound_json(plan, state["approved_plan"], label="approved plan")
        )
        validate_plan_against_source(approved_plan, state["source"]["probe"])
        require_identity(master, state["master"], label="master")
        evidence, checks = technical_checks(master)
        require_identity(master, state["master"], label="master after QA")
        validate_render_duration(approved_plan, evidence)

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            result = advance(current, Stage.QA_PASSED)
            result["qa"] = {
                "status": "PASS",
                "checked_at": utc_now(),
                "checks": checks,
                "probe": evidence,
            }
            return result

        return store.transact(mutate)


def create_manifest(root: Path) -> dict[str, Any]:
    with project_lease(root):
        store = StateStore(root)
        _recover_locked(root, store)
        state = store.load()
        if Stage(state["stage"]) is not Stage.QA_PASSED:
            raise ContractError("manifest creation requires QA_PASSED stage")
        for label in ("source", "approved_plan", "master"):
            require_identity(
                _artifact_path(root, state[label]),
                state[label],
                label=label.replace("_", " "),
            )
        manifest = {
            "schema_version": 1,
            "release_id": f"sha256-{state['master']['sha256'][:16]}",
            "source": {
                "sha256": state["source"]["sha256"],
                "size": state["source"]["size"],
            },
            "approved_plan": {
                "sha256": state["approved_plan"]["sha256"],
                "gate": "human-approved",
            },
            "artifact": {
                "sha256": state["master"]["sha256"],
                "size": state["master"]["size"],
                "profile": state["master"]["profile"],
                "probe": state["master"]["probe"],
            },
            "qa": deepcopy(state["qa"]),
            "provenance": {
                "tool": "verified-video-pipeline",
                "version": __version__,
                "public_repository_fixture_policy": "synthetic-only",
            },
        }
        manifest_path = root / MANIFEST_NAME
        manifest_bytes = _write_exact_json(manifest_path, manifest)
        manifest_identity = {
            "path": _relative_path(root, manifest_path),
            **manifest_bytes,
            "created_at": utc_now(),
        }

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            result = advance(current, Stage.READY)
            result["release_manifest"] = manifest_identity
            return result

        return store.transact(mutate)


def recover(root: Path) -> dict[str, Any]:
    with project_lease(root):
        store = StateStore(root)
        _recover_locked(root, store)
        return store.load()


def _recover_locked(root: Path, store: StateStore) -> None:
    store.recover()
    state = store.load()
    receipt = root / BUILD_RECEIPT_NAME
    if not os.path.lexists(receipt):
        return
    stage = Stage(state["stage"])
    if stage is Stage.PLAN_APPROVED:
        _recover_build_locked(root, store, state)
        return
    if stage in {Stage.MASTER_READY, Stage.QA_PASSED, Stage.READY}:
        validated_receipt = _validate_receipt(receipt)
        if validated_receipt.get("source_sha256") != state["source"]["sha256"]:
            raise RecoveryRequired(
                "completed build receipt belongs to a different source"
            )
        if validated_receipt.get("plan_sha256") != state["approved_plan"]["sha256"]:
            raise RecoveryRequired(
                "completed build receipt belongs to a different approved plan"
            )
        if validated_receipt.get("master") != state["master"]:
            raise RecoveryRequired(
                "completed build receipt does not match registered master"
            )
        require_identity(
            _artifact_path(root, state["master"]), state["master"], label="master"
        )
        staged = root / STAGING_NAME
        if os.path.lexists(staged):
            raise RecoveryRequired(
                "unexpected staging entry remains after master registration"
            )
        atomic_unlink(receipt)
        return
    raise RecoveryRequired("build receipt is incompatible with current lifecycle stage")


def status(root: Path, *, verify: bool = False) -> dict[str, Any]:
    store = StateStore(root)
    state = store.load()
    stage = Stage(state["stage"])
    blockers: list[str] = []
    recovery_required = store.has_pending_recovery() or os.path.lexists(
        root / BUILD_RECEIPT_NAME
    )
    if recovery_required:
        blockers.append("RECOVERY_REQUIRED")
    else:
        if not isinstance(state.get("master"), dict) and os.path.lexists(
            root / MASTER_NAME
        ):
            blockers.append("UNREGISTERED_MASTER")
        if os.path.lexists(root / STAGING_NAME):
            blockers.append("UNREGISTERED_STAGING")
    for label in ("source", "approved_plan", "master", "release_manifest"):
        identity = state.get(label)
        if not isinstance(identity, dict):
            continue
        path = _artifact_path(root, identity)
        if not path.is_file():
            blockers.append(f"MISSING_{label.upper()}")
            continue
        if path.stat().st_size != identity["size"]:
            blockers.append(f"SIZE_MISMATCH_{label.upper()}")
            continue
        if verify:
            try:
                require_identity(path, identity, label=label.replace("_", " "))
            except IntegrityError:
                blockers.append(f"HASH_MISMATCH_{label.upper()}")

    next_actions = {
        Stage.REGISTERED: "approve-plan",
        Stage.PLAN_APPROVED: "build",
        Stage.MASTER_READY: "qa",
        Stage.QA_PASSED: "manifest",
        Stage.READY: "none",
    }
    next_action = next_actions[stage]
    if blockers:
        next_action = (
            "recover" if "RECOVERY_REQUIRED" in blockers else "operator-review"
        )
    return {
        "schema_version": 1,
        "content_id": state["content_id"],
        "stage": stage.value,
        "revision": state["revision"],
        "ready": stage is Stage.READY and not blockers,
        "blockers": blockers,
        "next_action": next_action,
        "verification": "sha256" if verify else "size-only",
    }


def run_demo(root: Path) -> dict[str, Any]:
    if root.exists() and any(root.iterdir()):
        raise ContractError("demo directory must not exist or must be empty")
    root.mkdir(parents=True, exist_ok=True)
    source = root / SOURCE_NAME
    plan = root / "demo-plan.json"
    generate_synthetic_video(source)
    atomic_write_json(
        plan,
        {
            "schema_version": 1,
            "profile": PROFILE,
            "trim": {"start_seconds": 0.25, "end_seconds": 2.75},
        },
    )
    initialize(root, source, "synthetic-demo")
    approve_plan(root, plan, "demo-human-gate")
    build(root)
    run_qa(root)
    create_manifest(root)
    return status(root, verify=True)
