"""Exact-byte identity checks for immutable pipeline inputs and artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from verified_video_pipeline.errors import ContractError, IntegrityError


_CHUNK_SIZE = 1024 * 1024
_MAX_EVIDENCE_BYTES = 1024 * 1024


def _safe_read_fd(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        raise ContractError(
            f"cannot open regular artifact: {path.name}: {detail}"
        ) from exc
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        os.close(fd)
        raise ContractError(f"artifact is not a regular file: {path.name}")
    return fd, before


def _verify_open_identity(
    path: Path, before: os.stat_result, after: os.stat_result
) -> None:
    try:
        current_path = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise IntegrityError(
            f"artifact path changed while reading: {path.name}"
        ) from exc
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    path_identity = (
        current_path.st_dev,
        current_path.st_ino,
        current_path.st_size,
        current_path.st_mtime_ns,
    )
    if before_identity != after_identity or after_identity != path_identity:
        raise IntegrityError(f"artifact changed while reading: {path.name}")


def stable_sha256_file(path: Path) -> dict[str, Any]:
    """Hash a regular file and reject a concurrent mutation during the read."""

    fd, before = _safe_read_fd(path)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while chunk := stream.read(_CHUNK_SIZE):
                digest.update(chunk)
        after = os.fstat(fd)
        _verify_open_identity(path, before, after)
        return {"sha256": digest.hexdigest(), "size": after.st_size}
    except OSError as exc:
        raise ContractError(f"cannot read artifact: {path.name}: {exc}") from exc
    finally:
        os.close(fd)


def stable_read_bytes(
    path: Path, *, max_bytes: int = _MAX_EVIDENCE_BYTES
) -> tuple[bytes, dict[str, Any]]:
    """Read one bounded regular file and bind returned bytes to its path identity."""

    fd, before = _safe_read_fd(path)
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ContractError(f"bounded evidence file is too large: {path.name}")
        after = os.fstat(fd)
        _verify_open_identity(path, before, after)
        return data, {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    except OSError as exc:
        raise ContractError(f"cannot read artifact: {path.name}: {exc}") from exc
    finally:
        os.close(fd)


def require_identity(path: Path, expected: dict[str, Any], *, label: str) -> None:
    actual = stable_sha256_file(path)
    if actual != {"sha256": expected.get("sha256"), "size": expected.get("size")}:
        raise IntegrityError(f"{label} bytes changed after approval: {path.name}")
