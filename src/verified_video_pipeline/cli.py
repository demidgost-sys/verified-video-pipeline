"""Command-line interface for the public reference implementation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from verified_video_pipeline import __version__
from verified_video_pipeline.errors import PipelineError
from verified_video_pipeline.media import binary_versions
from verified_video_pipeline.pipeline import (
    approve_plan,
    build,
    create_manifest,
    initialize,
    recover,
    run_demo,
    run_qa,
    status,
)


def _project_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vvp",
        description="Fail-closed release assurance for human-approved video artifacts.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="show the local FFmpeg contract")

    demo = subparsers.add_parser(
        "demo", help="run an offline synthetic end-to-end proof"
    )
    demo.add_argument("project")

    init = subparsers.add_parser("init", help="register exact source bytes")
    init.add_argument("project")
    init.add_argument("source")
    init.add_argument("--content-id", required=True)

    approve = subparsers.add_parser(
        "approve-plan", help="bind a human-reviewed edit plan"
    )
    approve.add_argument("project")
    approve.add_argument("plan")
    approve.add_argument("--reviewer", required=True)

    for command, help_text in (
        ("build", "render and atomically register a no-clobber master"),
        ("qa", "run exact-byte, profile, and strict-decode checks"),
        ("manifest", "create a sanitized exact-byte release manifest"),
        ("recover", "roll forward a valid interrupted transition"),
    ):
        child = subparsers.add_parser(command, help=help_text)
        child.add_argument("project")

    show = subparsers.add_parser("status", help="read state without mutating it")
    show.add_argument("project")
    show.add_argument(
        "--verify", action="store_true", help="rehash every registered artifact"
    )
    return parser


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            _emit(
                {"tool": f"verified-video-pipeline {__version__}", **binary_versions()}
            )
            return 0
        project = _project_path(args.project)
        if args.command == "demo":
            result = run_demo(project)
        elif args.command == "init":
            result = initialize(
                project, Path(args.source).expanduser().resolve(), args.content_id
            )
        elif args.command == "approve-plan":
            result = approve_plan(
                project, Path(args.plan).expanduser().resolve(), args.reviewer
            )
        elif args.command == "build":
            result = build(project)
        elif args.command == "qa":
            result = run_qa(project)
        elif args.command == "manifest":
            result = create_manifest(project)
        elif args.command == "recover":
            result = recover(project)
        elif args.command == "status":
            result = status(project, verify=args.verify)
        else:  # pragma: no cover - argparse guarantees this branch is unreachable.
            raise AssertionError(args.command)
        _emit(result)
        return 0
    except PipelineError as exc:
        print(f"ERROR [{type(exc).__name__}] {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        name = Path(exc.filename).name if exc.filename else "required file"
        print(f"ERROR [ContractError] required file not found: {name}", file=sys.stderr)
        return 2
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        print(f"ERROR [OSError] local operation failed: {detail}", file=sys.stderr)
        return 2
