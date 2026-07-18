#!/usr/bin/env python3
"""Fail the public release gate on private or non-source repository material."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1024 * 1024
RUNTIME_BASENAMES = {
    ".master.staged.mp4",
    ".vvp.lock",
    "approved-plan.json",
    "build-receipt.json",
    "demo-plan.json",
    "journal.json",
    "project.json",
    "release-manifest.json",
}
DENIED_SUFFIXES = {
    ".7z",
    ".avi",
    ".fcpxml",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".key",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".p12",
    ".pdf",
    ".pem",
    ".pfx",
    ".png",
    ".srt",
    ".wav",
    ".webm",
    ".zip",
}

# audit-literals:start
DENIED_TEXT = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"/(?:Users|Volumes)/",
        r"/home/[a-z0-9._-]+/",
        r"[A-Z]:\\Users\\[^\\\s]+\\",
        r"@[a-z0-9.-]+\.(?:local|lan)\b",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bAIza[0-9A-Za-z_-]{30,}\b",
        r"\bya29\.[0-9A-Za-z_-]+",
        r"\bgh[pousr]_[0-9A-Za-z]{20,}\b",
        r"\bglpat-[0-9A-Za-z_-]{20,}\b",
        r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bsk_live_[0-9A-Za-z]{16,}\b",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
        r'"(?:access_token|refresh_token|client_secret)"\s*:',
        r'"(?:api_key|password|passwd|secret_key)"\s*:\s*"[^"\r\n]+"',
        r"youtube\.com/watch\?v=",
        r"youtu\.be/[0-9A-Za-z_-]{6,}",
        r"(?<![0-9A-Za-z_-])UC[0-9A-Za-z_-]{22}(?![0-9A-Za-z_-])",
        r"(?<![0-9A-Za-z_-])PL[0-9A-Za-z_-]{10,32}(?![0-9A-Za-z_-])",
        r"(?<![0-9A-Za-z_-])(?:UU|LL|FL)[0-9A-Za-z_-]{22}(?![0-9A-Za-z_-])",
    )
]
DENIED_TEXT_MATCH_ALLOWLIST = frozenset({"PLAN_APPROVED", "plan_identity"})
EMAIL_ADDRESS = re.compile(
    r"(?<![A-Z0-9._%+\-])"
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}\[\]~-]+"
    r"@[A-Z0-9](?:[A-Z0-9.-]{0,251}[A-Z0-9])?\.[A-Z]{2,63}"
    r"(?![A-Z0-9._%+\-])",
    re.IGNORECASE,
)
GITHUB_NOREPLY_EMAIL = re.compile(
    r"(?:[^@\s<>]+@users\.noreply\.github\.com|noreply@github\.com)\Z",
    re.IGNORECASE,
)
PUBLIC_SERVICE_EMAILS = frozenset({"support@github.com"})
GIT_IDENTITY = re.compile(r"<([^<>\r\n]+)>\s+\d+\s+[+-]\d{4}\Z")
# audit-literals:end

SENSITIVE_NAME = re.compile(
    r"(?:^|[-_.])(?:client[-_]?secret|credentials?|cookies?|secrets?|tokens?)(?:[-_.]|$)",
    re.IGNORECASE,
)


def _run_git(*arguments: str, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=text,
    )


def candidate_paths() -> list[Path]:
    result = _run_git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def denied_filename(relative: str) -> str | None:
    name = Path(relative).name
    lowered = name.lower()
    suffix = Path(lowered).suffix
    if lowered in RUNTIME_BASENAMES:
        return "runtime evidence filename"
    if lowered in {".env", ".envrc", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"}:
        return "credential filename"
    if lowered.startswith(".env."):
        return "environment filename"
    if suffix in DENIED_SUFFIXES:
        return "binary/media/credential suffix"
    if suffix in {".json", ".txt", ".yaml", ".yml"} and SENSITIVE_NAME.search(lowered):
        return "credential-shaped filename"
    return None


def _is_github_noreply(email: str) -> bool:
    return GITHUB_NOREPLY_EMAIL.fullmatch(email) is not None


def audit_text(text: str, *, origin: str, subject: str) -> list[str]:
    failures: list[str] = []
    for pattern in DENIED_TEXT:
        if any(
            match.group(0) not in DENIED_TEXT_MATCH_ALLOWLIST
            for match in pattern.finditer(text)
        ):
            failures.append(
                f"{origin}: private-data pattern {pattern.pattern!r}: {subject}"
            )
    for match in EMAIL_ADDRESS.finditer(text):
        email = match.group(0)
        if (
            not _is_github_noreply(email)
            and email.casefold() not in PUBLIC_SERVICE_EMAILS
        ):
            failures.append(f"{origin}: private email address: {subject}")
    return failures


def audit_blob(relative: str, data: bytes, *, origin: str) -> list[str]:
    failures = audit_text(relative, origin=origin, subject=f"path {relative}")
    reason = denied_filename(relative)
    if reason:
        failures.append(f"{origin}: {reason}: {relative}")
    if len(data) > MAX_BYTES:
        failures.append(f"{origin}: file exceeds {MAX_BYTES} bytes: {relative}")
    if b"\0" in data:
        failures.append(f"{origin}: binary content is forbidden: {relative}")
        return failures
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        failures.append(f"{origin}: non-UTF-8 content is forbidden: {relative}")
        return failures
    failures.extend(audit_text(text, origin=origin, subject=relative))
    return failures


def audit_worktree() -> list[str]:
    failures: list[str] = []
    for path in candidate_paths():
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            failures.append(f"worktree: symlink is forbidden: {relative}")
            continue
        if not path.is_file():
            failures.append(f"worktree: non-regular candidate: {relative}")
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            failures.append(
                f"worktree: file exceeds {MAX_BYTES} bytes: {relative} ({size})"
            )
            continue
        failures.extend(audit_blob(relative, path.read_bytes(), origin="worktree"))
    return failures


def _decode_git_object(
    object_id: str, object_type: str, data: bytes
) -> tuple[str | None, list[str]]:
    origin = f"history:{object_type}:{object_id[:12]}"
    if len(data) > MAX_BYTES:
        return None, [
            f"{origin}: metadata object exceeds {MAX_BYTES} bytes ({len(data)})"
        ]
    if b"\0" in data:
        return None, [f"{origin}: NUL byte in metadata object"]
    try:
        return data.decode("utf-8"), []
    except UnicodeDecodeError:
        return None, [f"{origin}: non-UTF-8 metadata object"]


def _audit_git_identity(identity: str, *, origin: str, role: str) -> list[str]:
    match = GIT_IDENTITY.search(identity)
    if match is None:
        return [f"{origin}: malformed Git {role} identity"]
    failures = audit_text(
        identity[: match.start(1)] + "git-email" + identity[match.end(1) :],
        origin=origin,
        subject=f"Git {role} identity",
    )
    if not _is_github_noreply(match.group(1)):
        failures.append(f"{origin}: private Git {role} email")
    return failures


def _audit_metadata_object(object_id: str, object_type: str, data: bytes) -> list[str]:
    origin = f"history:{object_type}:{object_id[:12]}"
    text, failures = _decode_git_object(object_id, object_type, data)
    if text is None:
        return failures
    try:
        headers, message = text.split("\n\n", 1)
    except ValueError:
        return [*failures, f"{origin}: metadata object has no message separator"]

    failures.extend(audit_text(headers, origin=origin, subject="Git metadata headers"))
    required_roles = ("author", "committer") if object_type == "commit" else ("tagger",)
    header_lines = headers.splitlines()
    for role in required_roles:
        prefix = f"{role} "
        identities = [
            line[len(prefix) :] for line in header_lines if line.startswith(prefix)
        ]
        if len(identities) != 1:
            failures.append(f"{origin}: expected exactly one Git {role} identity")
            continue
        failures.extend(_audit_git_identity(identities[0], origin=origin, role=role))

    subject = "commit message" if object_type == "commit" else "annotated tag message"
    failures.extend(audit_text(message, origin=origin, subject=subject))
    return failures


def _audit_reachable_tree_paths(object_types: dict[str, str]) -> list[str]:
    failures: list[str] = []
    root_trees = {
        _run_git("rev-parse", f"{object_id}^{{tree}}", text=True).stdout.strip()
        for object_id, object_type in object_types.items()
        if object_type == "commit"
    }
    seen_entries: set[tuple[str, str, str, str]] = set()
    seen_blob_ids: set[str] = set()
    blob_data: dict[str, bytes] = {}
    blob_sizes: dict[str, int] = {}

    for tree_id in sorted(root_trees):
        records = _run_git("ls-tree", "-rz", "--full-tree", "-r", tree_id).stdout.split(
            b"\0"
        )
        for record in records:
            if not record:
                continue
            try:
                header, raw_path = record.split(b"\t", 1)
                raw_mode, raw_type, raw_object_id = header.split(b" ", 2)
                mode = raw_mode.decode("ascii")
                object_type = raw_type.decode("ascii")
                object_id = raw_object_id.decode("ascii")
                relative = raw_path.decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                failures.append(
                    f"history:tree:{tree_id[:12]}: malformed or non-UTF-8 tree entry"
                )
                continue

            entry_key = (mode, object_type, object_id, relative)
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)
            if object_type != "blob":
                failures.append(
                    f"history:tree:{tree_id[:12]}: non-regular tree entry: {relative}"
                )
                continue
            seen_blob_ids.add(object_id)
            if mode not in {"100644", "100755"}:
                failures.append(
                    f"history:tree:{tree_id[:12]}: non-regular tree entry: {relative}"
                )
                continue

            object_size = blob_sizes.get(object_id)
            if object_size is None:
                object_size = int(
                    _run_git("cat-file", "-s", object_id, text=True).stdout.strip()
                )
                blob_sizes[object_id] = object_size
            if object_size > MAX_BYTES:
                failures.append(
                    f"history:{object_id[:12]}: file exceeds {MAX_BYTES} bytes: "
                    f"{relative} ({object_size})"
                )
                continue
            data = blob_data.get(object_id)
            if data is None:
                data = _run_git("cat-file", "blob", object_id).stdout
                blob_data[object_id] = data
            failures.extend(
                audit_blob(relative, data, origin=f"history:{object_id[:12]}")
            )

    uncontained_blobs = sorted(
        object_id
        for object_id, object_type in object_types.items()
        if object_type == "blob" and object_id not in seen_blob_ids
    )
    for object_id in uncontained_blobs:
        failures.append(
            f"history:{object_id[:12]}: reachable blob is not in a commit tree"
        )
    return failures


def audit_history() -> list[str]:
    failures: list[str] = []
    shallow = _run_git("rev-parse", "--is-shallow-repository", text=True).stdout.strip()
    if shallow != "false":
        failures.append("history: full Git history unavailable (shallow repository)")

    object_lines = _run_git(
        "rev-list", "--objects", "--no-object-names", "--all", text=True
    ).stdout.splitlines()
    object_types: dict[str, str] = {}
    for line in object_lines:
        object_id = line
        if object_id in object_types:
            continue
        object_type = _run_git("cat-file", "-t", object_id, text=True).stdout.strip()
        object_types[object_id] = object_type
        if object_type not in {"commit", "tag"}:
            continue
        data = _run_git("cat-file", object_type, object_id).stdout
        failures.extend(_audit_metadata_object(object_id, object_type, data))
    failures.extend(_audit_reachable_tree_paths(object_types))
    return failures


def audit() -> list[str]:
    return audit_worktree() + audit_history()


def main() -> int:
    try:
        failures = audit()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"PUBLIC AUDIT ERROR: {exc}", file=sys.stderr)
        return 2
    if failures:
        for failure in failures:
            print(f"PUBLIC AUDIT FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"PUBLIC AUDIT PASS: {len(candidate_paths())} source files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
