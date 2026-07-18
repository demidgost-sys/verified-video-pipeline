"""Fail-fast POSIX leases for project mutation and heavy media work."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from verified_video_pipeline.errors import ContractError, LeaseBusy


_LEASE_MARKER_PREFIX = b"VVP_FILE_LEASE_V1\n"
_MAX_LEASE_MARKER_BYTES = 4096


def _lease_marker(label: str, *, pid: int) -> bytes:
    if not isinstance(label, str):
        raise ContractError("lease label must be a string")
    payload = json.dumps(
        {"label": label, "pid": pid},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    marker = _LEASE_MARKER_PREFIX + payload + b"\n"
    if len(marker) > _MAX_LEASE_MARKER_BYTES:
        raise ContractError("lease label is too long")
    return marker


def _validate_lease_marker(marker: bytes, *, label: str) -> None:
    if len(marker) > _MAX_LEASE_MARKER_BYTES:
        raise ContractError(f"{label} lease marker is too large")
    if not marker.startswith(_LEASE_MARKER_PREFIX) or not marker.endswith(b"\n"):
        raise ContractError(f"{label} lease marker is invalid")
    payload_bytes = marker[len(_LEASE_MARKER_PREFIX) : -1]
    try:
        payload = json.loads(payload_bytes.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} lease marker is invalid") from exc
    if (
        type(payload) is not dict
        or set(payload) != {"label", "pid"}
        or type(payload["label"]) is not str
        or type(payload["pid"]) is not int
        or payload["pid"] <= 0
        or payload["label"] != label
    ):
        raise ContractError(f"{label} lease marker is invalid")
    if marker != _lease_marker(payload["label"], pid=payload["pid"]):
        raise ContractError(f"{label} lease marker is not canonical")


class FileLease:
    """An advisory lease that never waits in a hidden queue."""

    def __init__(self, path: Path, *, label: str):
        self.path = path
        self.label = label
        self._stream: TextIO | None = None

    def _assert_safe_identity(self, fd: int) -> os.stat_result:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ContractError(f"{self.label} lease path is not a regular file")
        if file_stat.st_nlink != 1:
            raise ContractError(f"{self.label} lease path has unexpected hard links")
        try:
            path_stat = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            detail = exc.strerror or type(exc).__name__
            raise ContractError(
                f"cannot verify {self.label} lease path safely: {detail}"
            ) from exc
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_nlink != 1
            or (path_stat.st_dev, path_stat.st_ino)
            != (file_stat.st_dev, file_stat.st_ino)
        ):
            raise ContractError(f"{self.label} lease path changed while opening")
        return file_stat

    def _read_and_validate_marker(self, fd: int) -> None:
        file_stat = self._assert_safe_identity(fd)
        if file_stat.st_size > _MAX_LEASE_MARKER_BYTES:
            raise ContractError(f"{self.label} lease marker is too large")
        marker = os.pread(fd, _MAX_LEASE_MARKER_BYTES + 1, 0)
        if len(marker) != file_stat.st_size:
            raise ContractError(f"{self.label} lease marker changed while reading")
        _validate_lease_marker(marker, label=self.label)

    def __enter__(self) -> "FileLease":
        marker = _lease_marker(self.label, pid=os.getpid())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        created = False
        try:
            fd = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            try:
                fd = os.open(self.path, flags)
            except OSError as exc:
                detail = exc.strerror or type(exc).__name__
                raise ContractError(
                    f"cannot open {self.label} lease safely: {detail}"
                ) from exc
        except OSError as exc:
            detail = exc.strerror or type(exc).__name__
            raise ContractError(
                f"cannot open {self.label} lease safely: {detail}"
            ) from exc

        stream: TextIO | None = None
        try:
            self._assert_safe_identity(fd)
            if not created:
                self._read_and_validate_marker(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LeaseBusy(
                    f"{self.label} lease is busy; retry after the owner exits"
                ) from exc
            self._assert_safe_identity(fd)
            if not created:
                self._read_and_validate_marker(fd)
            os.fchmod(fd, 0o600)
            stream = os.fdopen(fd, "r+", encoding="ascii")
            fd = -1
            stream.seek(0)
            stream.truncate()
            stream.write(marker.decode("ascii"))
            stream.flush()
            self._stream = stream
            return self
        except Exception:
            if stream is not None:
                stream.close()
            elif fd >= 0:
                os.close(fd)
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._stream is None:
            return
        fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()
        self._stream = None


@contextmanager
def project_lease(root: Path) -> Iterator[None]:
    with FileLease(root / ".vvp.lock", label="project"):
        yield


def heavy_lease_path() -> Path:
    override = os.environ.get("VVP_HEAVY_LEASE")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "verified-video-pipeline-v1.heavy.lock"


@contextmanager
def heavy_media_lease() -> Iterator[None]:
    with FileLease(heavy_lease_path(), label="heavy media"):
        yield
