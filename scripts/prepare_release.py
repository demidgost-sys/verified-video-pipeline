#!/usr/bin/env python3
"""Prepare deterministic, fail-closed assets for a tagged GitHub release."""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAME = "verified-video-pipeline"
MODULE_NAME = "verified_video_pipeline"
WORKFLOW_PATH = ".github/workflows/release.yml"
TAG_PATTERN = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
CHECKSUM_LINE_PATTERN = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$")
VERIFICATION_CHECKS = (
    "public_tree_audit",
    "compileall",
    "ruff_check",
    "ruff_format_check",
    "unit_tests",
    "clean_wheel_install",
    "synthetic_ffmpeg_demo",
)


class ReleaseError(RuntimeError):
    """A release invariant was not satisfied."""


def _git(
    *arguments: str,
    root: Path = ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _git_output(*arguments: str, root: Path = ROOT) -> str:
    return _git(*arguments, root=root).stdout.strip()


def version_from_tag(tag: str) -> str:
    if not TAG_PATTERN.fullmatch(tag):
        raise ReleaseError(
            "tag must be canonical stable SemVer in the form vMAJOR.MINOR.PATCH"
        )
    return tag[1:]


def expected_names(version: str) -> tuple[str, str]:
    return (
        f"{PROJECT_NAME}-{version}.tar.gz",
        f"{MODULE_NAME}-{version}-py3-none-any.whl",
    )


def _module_version(root: Path) -> str:
    path = root / "src" / MODULE_NAME / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    canonical: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if len(targets) != 1 or not (
            isinstance(targets[0], ast.Name) and targets[0].id == "__version__"
        ):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            canonical.append(value.value)

    writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "__version__"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    if len(canonical) != 1 or len(writes) != 1:
        raise ReleaseError("src package must contain exactly one literal __version__")
    return canonical[0]


def _project_table(text: str) -> dict[str, object]:
    document = tomllib.loads(text)
    project = document.get("project")
    if not isinstance(project, dict):
        raise ReleaseError("pyproject.toml must contain a project table")
    return project


def _require_dependency_free_project(project: dict[str, object]) -> None:
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or dependencies:
        raise ReleaseError("pyproject.toml project.dependencies must be empty")
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list):
        raise ReleaseError("pyproject.toml project.dynamic must be a list")
    if "dependencies" in dynamic:
        raise ReleaseError("pyproject.toml dependencies must not be dynamic")
    if "optional-dependencies" in dynamic:
        raise ReleaseError("pyproject.toml optional dependencies must not be dynamic")


def _expected_optional_metadata(
    project: dict[str, object],
) -> tuple[list[str], list[str]]:
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        raise ReleaseError(
            "pyproject.toml project.optional-dependencies must be a table"
        )
    extras: list[str] = []
    requirements: list[str] = []
    for raw_extra, raw_requirements in optional.items():
        if not isinstance(raw_extra, str) or not isinstance(raw_requirements, list):
            raise ReleaseError("pyproject.toml optional dependency group is invalid")
        extra = re.sub(r"[-_.]+", "-", raw_extra).lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", extra):
            raise ReleaseError("pyproject.toml optional dependency extra is invalid")
        if extra in extras:
            raise ReleaseError("pyproject.toml optional dependency extras collide")
        extras.append(extra)
        for requirement in raw_requirements:
            if not isinstance(requirement, str) or not requirement:
                raise ReleaseError("pyproject.toml optional dependency is invalid")
            if any(character in requirement for character in (";", "\n", "\r")):
                raise ReleaseError(
                    "optional dependency markers are unsupported by release verification"
                )
            requirements.append(f'{requirement}; extra == "{extra}"')
    return sorted(extras), sorted(requirements)


def _declared_version(root: Path) -> str:
    project = _project_table((root / "pyproject.toml").read_text(encoding="utf-8"))
    _require_dependency_free_project(project)
    version = project.get("version")
    if not isinstance(version, str):
        raise ReleaseError("pyproject.toml must contain a literal project.version")
    module_version = _module_version(root)
    if version != module_version:
        raise ReleaseError(
            f"version mismatch: pyproject.toml={version!r}, __version__={module_version!r}"
        )
    return version


def _resolve_commit(revision: str, *, root: Path) -> str:
    commit = _git_output("rev-parse", "--verify", f"{revision}^{{commit}}", root=root)
    if not OBJECT_ID_PATTERN.fullmatch(commit):
        raise ReleaseError(f"Git returned a non-canonical object ID for {revision!r}")
    return commit


def _exact_object_id(revision: str, *, label: str, root: Path) -> str:
    result = _git("rev-parse", "--verify", revision, root=root, check=False)
    object_id = result.stdout.strip()
    if result.returncode != 0 or not OBJECT_ID_PATTERN.fullmatch(object_id):
        raise ReleaseError(f"{label} is not an existing canonical Git object ID")
    return object_id


def _object_type(object_id: str, *, label: str, root: Path) -> str:
    result = _git("cat-file", "-t", object_id, root=root, check=False)
    object_type = result.stdout.strip()
    if result.returncode != 0:
        raise ReleaseError(f"{label} does not exist in the supplied repository")
    return object_type


def _validate_release_identity(
    *,
    tag: str,
    source_ref: str,
    source_commit: str,
    tag_object: str,
    root: Path,
) -> None:
    version_from_tag(tag)
    expected_ref = f"refs/tags/{tag}"
    if source_ref != expected_ref:
        raise ReleaseError("source ref does not exactly match the requested tag")
    if not OBJECT_ID_PATTERN.fullmatch(source_commit):
        raise ReleaseError("source commit is not a canonical Git object ID")
    if not OBJECT_ID_PATTERN.fullmatch(tag_object):
        raise ReleaseError("tag object is not a canonical Git object ID")

    resolved_tag_object = _exact_object_id(source_ref, label="source ref", root=root)
    if resolved_tag_object != tag_object:
        raise ReleaseError("source ref does not point to the supplied tag object")
    if _object_type(tag_object, label="tag object", root=root) != "tag":
        raise ReleaseError("tag object is not an annotated tag")
    if _object_type(source_commit, label="source commit", root=root) != "commit":
        raise ReleaseError("source commit is not an exact commit object")

    tag_data = _git("cat-file", "-p", tag_object, root=root).stdout
    headers = tag_data.split("\n\n", 1)[0].splitlines()
    expected_headers = (
        f"object {source_commit}",
        "type commit",
        f"tag {tag}",
    )
    if tuple(headers[:3]) != expected_headers:
        raise ReleaseError(
            "annotated tag object does not exactly bind the tag and source commit"
        )


def validate_release(
    *,
    tag: str,
    source_ref: str,
    expected_sha: str,
    main_ref: str,
    event_name: str,
    run_attempt: str,
    root: Path = ROOT,
) -> dict[str, str]:
    version = version_from_tag(tag)
    if source_ref != f"refs/tags/{tag}":
        raise ReleaseError("source ref does not exactly match the requested tag")
    if event_name != "push":
        raise ReleaseError("releases are accepted only from a tag push event")
    if run_attempt != "1":
        raise ReleaseError(
            "workflow reruns are forbidden; create a new version instead"
        )
    if not OBJECT_ID_PATTERN.fullmatch(expected_sha):
        raise ReleaseError("event SHA is not a canonical Git object ID")
    if _declared_version(root) != version:
        raise ReleaseError(
            "tag version does not equal both package version declarations"
        )

    status = _git_output("status", "--porcelain=v1", "--untracked-files=all", root=root)
    if status:
        raise ReleaseError("release checkout is not clean")

    tag_ref = f"refs/tags/{tag}"
    if _git_output("cat-file", "-t", tag_ref, root=root) != "tag":
        raise ReleaseError("release tag must be an annotated tag object")
    tag_object = _git_output("rev-parse", "--verify", tag_ref, root=root)
    if not OBJECT_ID_PATTERN.fullmatch(tag_object):
        raise ReleaseError("annotated tag has a non-canonical object ID")

    source_commit = _resolve_commit(tag_ref, root=root)
    if _resolve_commit("HEAD", root=root) != source_commit:
        raise ReleaseError("checked-out HEAD is not the annotated tag target")
    if _resolve_commit(expected_sha, root=root) != source_commit:
        raise ReleaseError("event SHA does not resolve to the annotated tag target")

    _resolve_commit(main_ref, root=root)
    ancestor = _git(
        "merge-base",
        "--is-ancestor",
        source_commit,
        main_ref,
        root=root,
        check=False,
    )
    if ancestor.returncode != 0:
        raise ReleaseError("tag target is not reachable from origin/main")

    return {
        "source_commit": source_commit,
        "source_ref": source_ref,
        "tag": tag,
        "tag_object": tag_object,
        "version": version,
    }


def _write_github_output(path: Path, values: dict[str, str]) -> None:
    for key, value in values.items():
        if not re.fullmatch(r"[a-z_]+", key) or "\n" in value or "\r" in value:
            raise ReleaseError("unsafe GitHub output value")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key in sorted(values):
            handle.write(f"{key}={values[key]}\n")


def _source_epoch(
    revision: str,
    *,
    root: Path,
    enforce_environment: bool,
) -> int:
    raw_epoch = _git_output("show", "-s", "--format=%ct", revision, root=root)
    if not raw_epoch.isascii() or not raw_epoch.isdecimal():
        raise ReleaseError("tag target has an invalid commit timestamp")
    epoch = int(raw_epoch)
    requested = os.environ.get("SOURCE_DATE_EPOCH")
    if enforce_environment and requested is not None and requested != raw_epoch:
        raise ReleaseError("SOURCE_DATE_EPOCH does not equal the tag commit timestamp")
    return epoch


def _canonical_source_archive_bytes(
    *,
    version: str,
    revision: str,
    root: Path,
    enforce_source_date_epoch: bool = False,
) -> bytes:
    epoch = _source_epoch(
        revision,
        root=root,
        enforce_environment=enforce_source_date_epoch,
    )
    archive = subprocess.run(
        [
            "git",
            "-c",
            "tar.umask=0022",
            "archive",
            "--format=tar",
            f"--prefix={PROJECT_NAME}-{version}/",
            revision,
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    compressed_bytes = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=compressed_bytes,
        compresslevel=9,
        mtime=epoch,
    ) as compressed:
        compressed.write(archive)
    return compressed_bytes.getvalue()


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ReleaseError(f"refusing to overwrite release asset: {path.name}") from exc


def create_source_archive(*, tag: str, output_dir: Path, root: Path = ROOT) -> Path:
    version = version_from_tag(tag)
    if _git_output("cat-file", "-t", f"refs/tags/{tag}", root=root) != "tag":
        raise ReleaseError("source archive requires an annotated tag")
    source_name, _ = expected_names(version)
    destination = output_dir / source_name
    if destination.exists() or destination.is_symlink():
        raise ReleaseError(f"refusing to overwrite release asset: {source_name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ReleaseError("release output must be a real directory")

    archive = _canonical_source_archive_bytes(
        version=version,
        revision=f"refs/tags/{tag}^{{commit}}",
        root=root,
        enforce_source_date_epoch=True,
    )
    with tempfile.NamedTemporaryFile(dir=output_dir, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        temporary.write(archive)
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        os.link(temporary_path, destination)
    except FileExistsError as exc:
        raise ReleaseError(
            f"refusing to overwrite release asset: {source_name}"
        ) from exc
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ReleaseError(f"release asset is not a regular file: {path.name}")


def _validate_source_archive(
    path: Path,
    *,
    version: str,
    source_commit: str,
    root: Path,
) -> None:
    _require_regular_file(path)
    prefix = f"{PROJECT_NAME}-{version}/"
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
    except (tarfile.TarError, OSError) as exc:
        raise ReleaseError("source archive is not a readable tar.gz") from exc
    if not members:
        raise ReleaseError("source archive is empty")
    seen: set[str] = set()
    for member in members:
        if member.name in seen:
            raise ReleaseError("source archive contains duplicate paths")
        seen.add(member.name)
        if member.name != prefix.rstrip("/") and not member.name.startswith(prefix):
            raise ReleaseError("source archive path escaped its release prefix")
        relative = member.name.removeprefix(prefix)
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ReleaseError("source archive contains an unsafe path")
        if not (member.isfile() or member.isdir()):
            raise ReleaseError("source archive contains a non-file entry")
    expected = _canonical_source_archive_bytes(
        version=version,
        revision=source_commit,
        root=root,
    )
    if path.read_bytes() != expected:
        raise ReleaseError(
            "source archive does not exactly match canonical git archive bytes"
        )


def _validate_wheel(
    path: Path,
    *,
    version: str,
    project: dict[str, object],
) -> None:
    _require_regular_file(path)
    try:
        with zipfile.ZipFile(path) as wheel:
            metadata_paths = [
                name
                for name in wheel.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                raise ReleaseError("wheel must contain exactly one METADATA file")
            metadata = Parser().parsestr(
                wheel.read(metadata_paths[0]).decode("utf-8", errors="strict")
            )
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise ReleaseError("wheel is not a readable Python wheel") from exc
    if metadata.get("Name") != PROJECT_NAME or metadata.get("Version") != version:
        raise ReleaseError("wheel metadata does not match the release tag")
    expected_extras, expected_requirements = _expected_optional_metadata(project)
    actual_extras = sorted(metadata.get_all("Provides-Extra", []))
    actual_requirements = sorted(metadata.get_all("Requires-Dist", []))
    if actual_extras != expected_extras:
        raise ReleaseError("wheel METADATA Provides-Extra does not match pyproject")
    if actual_requirements != expected_requirements:
        raise ReleaseError(
            "wheel METADATA Requires-Dist is not the exact extra-gated dependency set"
        )


def _artifact_record(path: Path, *, kind: str) -> dict[str, object]:
    return {
        "kind": kind,
        "name": path.name,
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _canonical_json(data: object) -> bytes:
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _expected_evidence(
    *,
    tag: str,
    source_ref: str,
    source_commit: str,
    tag_object: str,
    source_path: Path,
    wheel_path: Path,
) -> dict[str, object]:
    version = version_from_tag(tag)
    if source_ref != f"refs/tags/{tag}":
        raise ReleaseError("evidence source ref does not match the tag")
    if not OBJECT_ID_PATTERN.fullmatch(source_commit):
        raise ReleaseError("evidence source commit is not a canonical object ID")
    if not OBJECT_ID_PATTERN.fullmatch(tag_object):
        raise ReleaseError("evidence tag object is not a canonical object ID")
    return {
        "artifacts": [
            _artifact_record(source_path, kind="source_archive"),
            _artifact_record(wheel_path, kind="python_wheel"),
        ],
        "build": {
            "checks": [
                {"name": name, "status": "passed"} for name in VERIFICATION_CHECKS
            ],
            "workflow": WORKFLOW_PATH,
        },
        "project": PROJECT_NAME,
        "schema_version": 1,
        "source": {
            "commit": source_commit,
            "ref": source_ref,
            "tag_kind": "annotated",
            "tag_object": tag_object,
        },
        "tag": tag,
        "version": version,
    }


def _asset_paths(
    release_dir: Path,
    *,
    version: str,
    source_commit: str,
    root: Path,
) -> tuple[Path, Path]:
    source_name, wheel_name = expected_names(version)
    source_path = release_dir / source_name
    wheel_path = release_dir / wheel_name
    _validate_source_archive(
        source_path,
        version=version,
        source_commit=source_commit,
        root=root,
    )
    tagged_pyproject = _git("show", f"{source_commit}:pyproject.toml", root=root).stdout
    project = _project_table(tagged_pyproject)
    _require_dependency_free_project(project)
    _validate_wheel(wheel_path, version=version, project=project)
    return source_path, wheel_path


def finalize_release(
    *,
    tag: str,
    source_ref: str,
    source_commit: str,
    tag_object: str,
    release_dir: Path,
    root: Path = ROOT,
) -> tuple[Path, Path]:
    version = version_from_tag(tag)
    _validate_release_identity(
        tag=tag,
        source_ref=source_ref,
        source_commit=source_commit,
        tag_object=tag_object,
        root=root,
    )
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise ReleaseError("release output must be a real directory")
    source_path, wheel_path = _asset_paths(
        release_dir,
        version=version,
        source_commit=source_commit,
        root=root,
    )
    existing = sorted(path.name for path in release_dir.iterdir())
    expected_existing = sorted((source_path.name, wheel_path.name))
    if existing != expected_existing:
        raise ReleaseError(
            "release directory contains unexpected files before finalize"
        )

    evidence_path = release_dir / "release-evidence.json"
    checksums_path = release_dir / "SHA256SUMS"
    evidence = _expected_evidence(
        tag=tag,
        source_ref=source_ref,
        source_commit=source_commit,
        tag_object=tag_object,
        source_path=source_path,
        wheel_path=wheel_path,
    )
    _write_new(evidence_path, _canonical_json(evidence))

    checksum_paths = sorted(
        (source_path, wheel_path, evidence_path), key=lambda p: p.name
    )
    checksums = "".join(f"{_sha256(path)}  {path.name}\n" for path in checksum_paths)
    _write_new(checksums_path, checksums.encode("ascii"))
    verify_release(
        tag=tag,
        source_ref=source_ref,
        source_commit=source_commit,
        tag_object=tag_object,
        release_dir=release_dir,
        root=root,
    )
    return evidence_path, checksums_path


def verify_release(
    *,
    tag: str,
    source_ref: str,
    source_commit: str,
    tag_object: str,
    release_dir: Path,
    root: Path = ROOT,
) -> None:
    version = version_from_tag(tag)
    _validate_release_identity(
        tag=tag,
        source_ref=source_ref,
        source_commit=source_commit,
        tag_object=tag_object,
        root=root,
    )
    source_path, wheel_path = _asset_paths(
        release_dir,
        version=version,
        source_commit=source_commit,
        root=root,
    )
    evidence_path = release_dir / "release-evidence.json"
    checksums_path = release_dir / "SHA256SUMS"
    expected_files = sorted(
        (source_path.name, wheel_path.name, evidence_path.name, checksums_path.name)
    )
    actual_files = sorted(path.name for path in release_dir.iterdir())
    if actual_files != expected_files:
        raise ReleaseError("release directory does not contain the exact asset set")
    for path in (evidence_path, checksums_path):
        _require_regular_file(path)

    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("release evidence is not valid UTF-8 JSON") from exc
    if evidence_path.read_bytes() != _canonical_json(evidence):
        raise ReleaseError("release evidence is not in canonical JSON form")
    expected_evidence = _expected_evidence(
        tag=tag,
        source_ref=source_ref,
        source_commit=source_commit,
        tag_object=tag_object,
        source_path=source_path,
        wheel_path=wheel_path,
    )
    if evidence != expected_evidence:
        raise ReleaseError("release evidence does not match the exact assets")

    checksum_lines = checksums_path.read_text(encoding="ascii").splitlines()
    expected_checksum_names = sorted(
        (source_path.name, wheel_path.name, evidence_path.name)
    )
    if len(checksum_lines) != len(expected_checksum_names):
        raise ReleaseError("SHA256SUMS has an unexpected number of entries")
    parsed: list[tuple[str, str]] = []
    for line in checksum_lines:
        match = CHECKSUM_LINE_PATTERN.fullmatch(line)
        if match is None:
            raise ReleaseError("SHA256SUMS is not in strict sha256sum format")
        parsed.append((match.group(2), match.group(1)))
    if [name for name, _ in parsed] != expected_checksum_names:
        raise ReleaseError("SHA256SUMS asset names are not canonical")
    for name, expected_digest in parsed:
        if _sha256(release_dir / name) != expected_digest:
            raise ReleaseError(f"checksum mismatch: {name}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="validate the tag and repository")
    check.add_argument("--tag", required=True)
    check.add_argument("--source-ref", required=True)
    check.add_argument("--expected-sha", required=True)
    check.add_argument("--main-ref", required=True)
    check.add_argument("--event-name", required=True)
    check.add_argument("--run-attempt", required=True)
    check.add_argument("--github-output", type=Path)

    archive = subparsers.add_parser("archive", help="build the tagged source archive")
    archive.add_argument("--tag", required=True)
    archive.add_argument("--output-dir", type=Path, required=True)

    for command in ("finalize", "verify"):
        subparser = subparsers.add_parser(
            command, help=f"{command} the exact release asset set"
        )
        subparser.add_argument("--tag", required=True)
        subparser.add_argument("--source-ref", required=True)
        subparser.add_argument("--source-commit", required=True)
        subparser.add_argument("--tag-object", required=True)
        subparser.add_argument("--release-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "check":
            values = validate_release(
                tag=args.tag,
                source_ref=args.source_ref,
                expected_sha=args.expected_sha,
                main_ref=args.main_ref,
                event_name=args.event_name,
                run_attempt=args.run_attempt,
            )
            if args.github_output is not None:
                _write_github_output(args.github_output, values)
            print(json.dumps(values, sort_keys=True))
        elif args.command == "archive":
            path = create_source_archive(tag=args.tag, output_dir=args.output_dir)
            print(path.name)
        elif args.command == "finalize":
            evidence, checksums = finalize_release(
                tag=args.tag,
                source_ref=args.source_ref,
                source_commit=args.source_commit,
                tag_object=args.tag_object,
                release_dir=args.release_dir,
            )
            print(f"{evidence.name}\n{checksums.name}")
        elif args.command == "verify":
            verify_release(
                tag=args.tag,
                source_ref=args.source_ref,
                source_commit=args.source_commit,
                tag_object=args.tag_object,
                release_dir=args.release_dir,
            )
            print("RELEASE ASSET VERIFICATION PASS")
        else:  # pragma: no cover - argparse prevents this path
            raise ReleaseError(f"unsupported command: {args.command}")
    except (
        ReleaseError,
        OSError,
        subprocess.CalledProcessError,
        tarfile.TarError,
        tomllib.TOMLDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"RELEASE PREP FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
