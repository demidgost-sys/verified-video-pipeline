#!/usr/bin/env python3
"""Fail closed on the latest CI/CodeQL runs for the exact main commit."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


REPOSITORY = "demidgost-sys/verified-video-pipeline"
WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/codeql.yml")


def select_checks(runs, commit):
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit):
        raise ValueError("invalid release commit")
    if not isinstance(runs, list):
        raise ValueError("invalid workflow-run response")
    links = []
    for path in WORKFLOWS:
        matches = [
            r
            for r in runs
            if isinstance(r, dict)
            and r.get("path") == path
            and r.get("head_sha") == commit
            and r.get("event") == "push"
            and r.get("head_branch") == "main"
        ]
        if not matches or any(
            type(r.get("id")) is not int or r["id"] <= 0 for r in matches
        ):
            raise ValueError(f"missing exact main-commit workflow: {path}")
        latest = max(matches, key=lambda r: r["id"])
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            raise ValueError(f"latest exact-commit workflow has not succeeded: {path}")
        links.append(
            (path, f"https://github.com/{REPOSITORY}/actions/runs/{latest['id']}")
        )
    return links


def release_notes(evidence, links, run_url):
    lines = [
        f"Verified Video Pipeline {evidence['version']}",
        "",
        f"Source commit: `{evidence['source']['commit']}`",
        f"Annotated tag object: `{evidence['source']['tag_object']}`",
        "",
        f"Release gate: {run_url}",
        *[f"{path}: {url}" for path, url in links],
        "",
        "Evidence schema: 2. Project and release-manifest schemas: 2; engine schema: 1.",
        "Loudness: synthetic-web-loudness@1, -16 LUFS +/-1 LU; true peak <= -1 dBTP.",
        "Wheel rebuilt from the canonical source archive and compared byte for byte.",
        "",
        "SHA-256 (also verify SHA256SUMS and GitHub Artifact Attestations):",
    ]
    lines.extend(f"- `{a['name']}`: `{a['sha256']}`" for a in evidence["artifacts"])
    lines.extend(
        [
            "",
            "Known limits: synthetic fixtures only; POSIX local filesystems on Linux/macOS.",
            "Native Windows is unsupported. WSL is a Linux test environment, not Windows certification.",
            "The engine canary detects relevant sampled behavior, not every possible codec change.",
            "Technical loudness does not replace human listening or editorial approval.",
            "OIDC attestations prove workflow/source identity, not independent human validation.",
            "No PyPI distribution, production fixtures, or live upload/publishing adapters.",
            "See docs/release-process.md and docs/v0.2-contract.md in the tagged source.",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{REPOSITORY}/actions/runs?head_sha={args.commit}&per_page=100",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        links = select_checks(json.loads(result.stdout)["workflow_runs"], args.commit)
        evidence = json.loads((args.release_dir / "release-evidence.json").read_text())
        if (
            evidence["source"]["commit"] != args.commit
            or evidence["schema_version"] != 2
        ):
            raise ValueError("release evidence does not match the exact commit/schema")
        if not re.fullmatch(
            rf"https://github.com/{REPOSITORY}/actions/runs/[0-9]+", args.run_url
        ):
            raise ValueError("invalid release run URL")
        args.notes_file.write_text(
            release_notes(evidence, links, args.run_url), encoding="utf-8"
        )
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(f"RELEASE CHECKS FAIL: {exc}")
        return 1
    print("EXACT-COMMIT CI AND CODEQL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
