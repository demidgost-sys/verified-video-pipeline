from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_release_module():
    path = ROOT / "scripts" / "prepare_release.py"
    spec = importlib.util.spec_from_file_location("vvp_prepare_release_hardening", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load release preparation module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load_release_module()


def run_git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def initialize_repository(
    root: Path,
    *,
    dependencies: str = "[]",
    optional_dependencies: bool = False,
) -> tuple[str, str]:
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.name", "Synthetic Maintainer")
    run_git(root, "config", "user.email", "maintainer@users.noreply.github.com")
    package = root / "src" / "verified_video_pipeline"
    package.mkdir(parents=True)
    optional = ""
    if optional_dependencies:
        optional = (
            "\n[project.optional-dependencies]\n"
            'dev = ["jsonschema==4.26.0", "ruff==0.15.22"]\n'
        )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "verified-video-pipeline"\n'
        'version = "0.2.0"\n'
        f"dependencies = {dependencies}\n"
        f"{optional}",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    (root / "fixture.txt").write_text("synthetic fixture\n", encoding="utf-8")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "synthetic release fixture")
    run_git(root, "tag", "-a", "v0.2.0", "-m", "Release v0.2.0")
    commit = run_git(root, "rev-parse", "HEAD")
    tag_object = run_git(root, "rev-parse", "refs/tags/v0.2.0")
    return commit, tag_object


def write_wheel(
    release_dir: Path,
    *,
    requires_dist: list[str] | None = None,
    provides_extra: list[str] | None = None,
) -> Path:
    _, name = release.expected_names("0.2.0")
    path = release_dir / name
    dist_info = "verified_video_pipeline-0.2.0.dist-info"
    metadata = "Metadata-Version: 2.4\nName: verified-video-pipeline\nVersion: 0.2.0\n"
    for extra in provides_extra or []:
        metadata += f"Provides-Extra: {extra}\n"
    for requirement in requires_dist or []:
        metadata += f"Requires-Dist: {requirement}\n"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(f"{dist_info}/METADATA", metadata)
        wheel.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: synthetic-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
    return path


def create_source_archive(root: Path, release_dir: Path, commit: str) -> Path:
    epoch = run_git(root, "show", "-s", "--format=%ct", commit)
    with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": epoch}):
        return release.create_source_archive(
            tag="v0.2.0", output_dir=release_dir, root=root
        )


def forge_evidence_and_checksums(
    release_dir: Path,
    *,
    commit: str,
    tag_object: str,
) -> None:
    source_name, wheel_name = release.expected_names("0.2.0")
    source_path = release_dir / source_name
    wheel_path = release_dir / wheel_name
    evidence_path = release_dir / "release-evidence.json"
    evidence = release._expected_evidence(
        tag="v0.2.0",
        source_ref="refs/tags/v0.2.0",
        source_commit=commit,
        tag_object=tag_object,
        source_path=source_path,
        wheel_path=wheel_path,
    )
    evidence_path.write_bytes(release._canonical_json(evidence))
    checksum_paths = sorted(
        (source_path, wheel_path, evidence_path), key=lambda path: path.name
    )
    (release_dir / "SHA256SUMS").write_text(
        "".join(f"{release._sha256(path)}  {path.name}\n" for path in checksum_paths),
        encoding="ascii",
    )


def finalize(
    root: Path,
    release_dir: Path,
    commit: str,
    tag_object: str,
) -> None:
    release.finalize_release(
        tag="v0.2.0",
        source_ref="refs/tags/v0.2.0",
        source_commit=commit,
        tag_object=tag_object,
        release_dir=release_dir,
        root=root,
    )


def verify(
    root: Path,
    release_dir: Path,
    commit: str,
    tag_object: str,
) -> None:
    release.verify_release(
        tag="v0.2.0",
        source_ref="refs/tags/v0.2.0",
        source_commit=commit,
        tag_object=tag_object,
        release_dir=release_dir,
        root=root,
    )


class CanonicalArchiveTests(unittest.TestCase):
    def test_git_tar_umask_cannot_change_source_archive_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            commit, _ = initialize_repository(root)
            run_git(root, "config", "tar.umask", "0022")
            canonical = create_source_archive(root, base / "release-a", commit)
            run_git(root, "config", "tar.umask", "0002")
            permissive = create_source_archive(root, base / "release-b", commit)
            run_git(root, "config", "tar.umask", "0077")
            restrictive = create_source_archive(root, base / "release-c", commit)
            self.assertEqual(canonical.read_bytes(), permissive.read_bytes())
            self.assertEqual(canonical.read_bytes(), restrictive.read_bytes())


class ReleaseBindingTests(unittest.TestCase):
    def test_finalize_and_verify_bind_evidence_to_exact_tag_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            commit, tag_object = initialize_repository(root)
            release_dir = base / "release"
            create_source_archive(root, release_dir, commit)
            write_wheel(release_dir)
            finalize(root, release_dir, commit, tag_object)
            verify(root, release_dir, commit, tag_object)
            evidence = json.loads(
                (release_dir / "release-evidence.json").read_text(encoding="utf-8")
            )
            self.assertEqual(evidence["source"]["tag_object"], tag_object)

    def test_finalize_rejects_safe_but_non_git_source_tar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            commit, tag_object = initialize_repository(root)
            release_dir = base / "release"
            release_dir.mkdir()
            source_name, _ = release.expected_names("0.2.0")
            with tarfile.open(release_dir / source_name, "w:gz") as archive:
                payload = b"not the tagged source\n"
                member = tarfile.TarInfo("verified-video-pipeline-0.2.0/FAKE.txt")
                member.mode = 0o644
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            write_wheel(release_dir)
            with self.assertRaisesRegex(release.ReleaseError, "canonical git archive"):
                finalize(root, release_dir, commit, tag_object)
            forge_evidence_and_checksums(
                release_dir,
                commit=commit,
                tag_object=tag_object,
            )
            with self.assertRaisesRegex(release.ReleaseError, "canonical git archive"):
                verify(root, release_dir, commit, tag_object)

    def test_verify_rejects_bogus_commit_and_moved_tag_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            commit, tag_object = initialize_repository(root)
            release_dir = base / "release"
            create_source_archive(root, release_dir, commit)
            write_wheel(release_dir)
            finalize(root, release_dir, commit, tag_object)
            with self.assertRaises(release.ReleaseError):
                verify(root, release_dir, "0" * len(commit), tag_object)

            run_git(root, "tag", "-d", "v0.2.0")
            run_git(
                root,
                "tag",
                "-a",
                "v0.2.0",
                commit,
                "-m",
                "Replacement tag object is forbidden",
            )
            with self.assertRaisesRegex(release.ReleaseError, "tag object"):
                verify(root, release_dir, commit, tag_object)

    def test_finalize_and_verify_cli_require_tag_object(self) -> None:
        for command in ("finalize", "verify"):
            with self.subTest(command=command):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        release._build_parser().parse_args(
                            [
                                command,
                                "--tag",
                                "v0.2.0",
                                "--source-ref",
                                "refs/tags/v0.2.0",
                                "--source-commit",
                                "0" * 40,
                                "--release-dir",
                                "release",
                            ]
                        )


class DependencyContractTests(unittest.TestCase):
    def test_declared_dev_extras_are_exactly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            commit, tag_object = initialize_repository(root, optional_dependencies=True)
            release_dir = base / "release"
            create_source_archive(root, release_dir, commit)
            write_wheel(
                release_dir,
                provides_extra=["dev"],
                requires_dist=[
                    'jsonschema==4.26.0; extra == "dev"',
                    'ruff==0.15.22; extra == "dev"',
                ],
            )
            finalize(root, release_dir, commit, tag_object)
            verify(root, release_dir, commit, tag_object)

    def test_nonempty_runtime_dependencies_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            commit, tag_object = initialize_repository(
                root, dependencies='["requests==2.32.4"]'
            )
            release_dir = base / "release"
            create_source_archive(root, release_dir, commit)
            write_wheel(release_dir)
            with self.assertRaisesRegex(release.ReleaseError, "must be empty"):
                finalize(root, release_dir, commit, tag_object)

    def test_unconditional_or_unknown_extra_requirement_is_rejected(self) -> None:
        bad_sets = (
            (["jsonschema==4.26.0"], ["dev"]),
            (
                ['jsonschema==4.26.0; extra == "dev" or python_version >= "3"'],
                ["dev"],
            ),
            (['jsonschema==4.26.0; extra == "release"'], ["release"]),
        )
        for requirements, extras in bad_sets:
            with self.subTest(requirements=requirements):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    root = base / "repo"
                    root.mkdir()
                    commit, tag_object = initialize_repository(
                        root, optional_dependencies=True
                    )
                    release_dir = base / "release"
                    create_source_archive(root, release_dir, commit)
                    write_wheel(
                        release_dir,
                        provides_extra=extras,
                        requires_dist=requirements,
                    )
                    with self.assertRaisesRegex(release.ReleaseError, "METADATA"):
                        finalize(root, release_dir, commit, tag_object)


class ModuleVersionTests(unittest.TestCase):
    def test_only_one_unconditional_literal_version_write_is_allowed(self) -> None:
        rejected = (
            '__version__ = "0.2.0"\n__version__ = "0.2.1"\n',
            'if True:\n    __version__ = "0.2.0"\n',
            ('__version__ = "0.2.0"\nif True:\n    __version__ = "0.2.1"\n'),
            '__version__ = "0.2.0"\n__version__ += ".dev"\n',
            '__version__ = "0.2.0"\ndel __version__\n',
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "src" / "verified_video_pipeline"
            package.mkdir(parents=True)
            init = package / "__init__.py"
            init.write_text('__version__ = "0.2.0"\n', encoding="utf-8")
            self.assertEqual(release._module_version(root), "0.2.0")
            for source in rejected:
                with self.subTest(source=source):
                    init.write_text(source, encoding="utf-8")
                    with self.assertRaises(release.ReleaseError):
                        release._module_version(root)


if __name__ == "__main__":
    unittest.main()
