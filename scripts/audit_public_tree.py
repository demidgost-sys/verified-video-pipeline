#!/usr/bin/env python3
"""Fail the public release gate on private or non-source repository material."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF_RELATIVE = Path(__file__).resolve().relative_to(ROOT).as_posix()
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
        r"[A-Z0-9._%+-]+@(?:gmail|googlemail)\.com\b",
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
    )
]
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


def _strip_policy_literals(relative: str, text: str) -> str:
    if relative != SELF_RELATIVE:
        return text
    return re.sub(
        r"# audit-literals:start.*?# audit-literals:end",
        "# audit literals intentionally omitted from self-scan",
        text,
        flags=re.DOTALL,
    )


def audit_blob(relative: str, data: bytes, *, origin: str) -> list[str]:
    failures: list[str] = []
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
    text = _strip_policy_literals(relative, text)
    for pattern in DENIED_TEXT:
        if pattern.search(text):
            failures.append(
                f"{origin}: private-data pattern {pattern.pattern!r}: {relative}"
            )
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


def audit_history() -> list[str]:
    failures: list[str] = []
    authors = _run_git("log", "--all", "--format=%ae", text=True).stdout.splitlines()
    for email in authors:
        if email.lower().endswith((".local", ".lan")):
            failures.append(f"history: private Git author email: {email}")

    object_lines = _run_git(
        "rev-list", "--objects", "--all", text=True
    ).stdout.splitlines()
    seen: set[tuple[str, str]] = set()
    for line in object_lines:
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        object_id, relative = parts
        object_key = (object_id, relative)
        if object_key in seen:
            continue
        object_type = _run_git("cat-file", "-t", object_id, text=True).stdout.strip()
        if object_type != "blob":
            continue
        seen.add(object_key)
        object_size = int(
            _run_git("cat-file", "-s", object_id, text=True).stdout.strip()
        )
        if object_size > MAX_BYTES:
            failures.append(
                f"history:{object_id[:12]}: file exceeds {MAX_BYTES} bytes: "
                f"{relative} ({object_size})"
            )
            continue
        data = _run_git("cat-file", "blob", object_id).stdout
        failures.extend(audit_blob(relative, data, origin=f"history:{object_id[:12]}"))
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
