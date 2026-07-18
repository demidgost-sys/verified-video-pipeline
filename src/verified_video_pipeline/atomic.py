"""Small durability primitives used by the state journal."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from verified_video_pipeline.errors import ContractError


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for hashing and compare-and-swap guards."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    from verified_video_pipeline.hashing import stable_read_bytes

    try:
        data, _ = stable_read_bytes(path)
        return parse_json_object(data, name=path.name)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContractError(f"invalid JSON file: {path.name}: {exc}") from exc
    except ContractError:
        raise


def parse_json_object(data: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=_reject_nonfinite)
    except (UnicodeError, ValueError) as exc:
        raise ContractError(f"invalid JSON file: {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {name}")
    return value


def fsync_directory(path: Path) -> None:
    """Ask the local filesystem to persist a directory entry update.

    This is a best-effort POSIX durability primitive, not a claim about sudden
    power-loss behavior on every filesystem or storage controller.
    """

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    """Write bytes through a same-directory temp file and atomic rename."""

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ContractError(f"refusing to replace symlink: {path.name}")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any, *, mode: int = 0o644) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value), mode=mode)


def atomic_unlink(path: Path) -> None:
    path.unlink()
    fsync_directory(path.parent)
