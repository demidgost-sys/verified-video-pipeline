from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_release_module():
    path = ROOT / "scripts" / "prepare_release.py"
    spec = importlib.util.spec_from_file_location("vvp_prepare_release", path)
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


def write_project(root: Path, version: str = "0.2.0") -> None:
    package = root / "src" / "verified_video_pipeline"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "verified-video-pipeline"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )


def initialize_tagged_repository(root: Path, version: str = "0.2.0") -> str:
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.name", "Synthetic Maintainer")
    run_git(root, "config", "user.email", "maintainer@users.noreply.github.com")
    write_project(root, version)
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "synthetic release fixture")
    run_git(root, "tag", "-a", f"v{version}", "-m", f"Release v{version}")
    commit = run_git(root, "rev-parse", "HEAD")
    run_git(root, "update-ref", "refs/remotes/origin/main", commit)
    return commit


def write_synthetic_wheel(release_dir: Path, version: str = "0.2.0") -> Path:
    _, wheel_name = release.expected_names(version)
    wheel_path = release_dir / wheel_name
    dist_info = f"verified_video_pipeline-{version}.dist-info"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.4\nName: verified-video-pipeline\nVersion: {version}\n",
        )
        wheel.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: synthetic-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
    return wheel_path


def rewrite_evidence_and_checksums(release_dir: Path, evidence: dict) -> None:
    evidence_path = release_dir / "release-evidence.json"
    evidence_path.write_bytes(release._canonical_json(evidence))
    source_name, wheel_name = release.expected_names("0.2.0")
    paths = sorted(
        (release_dir / source_name, release_dir / wheel_name, evidence_path),
        key=lambda path: path.name,
    )
    checksums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in paths
    )
    (release_dir / "SHA256SUMS").write_text(checksums, encoding="ascii")


class ReleaseIdentityTests(unittest.TestCase):
    def test_only_canonical_stable_semver_tags_are_accepted(self) -> None:
        self.assertEqual(release.version_from_tag("v0.2.0"), "0.2.0")
        self.assertEqual(release.version_from_tag("v12.34.56"), "12.34.56")
        for tag in (
            "0.2.0",
            "v01.2.0",
            "v1.02.0",
            "v1.2.03",
            "v1.2",
            "v1.2.3-rc.1",
            "v1.2.3+build",
            "v1.2.3/extra",
        ):
            with self.subTest(tag=tag):
                with self.assertRaises(release.ReleaseError):
                    release.version_from_tag(tag)

    def test_valid_release_is_bound_to_annotated_tag_and_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = initialize_tagged_repository(root)
            values = release.validate_release(
                tag="v0.2.0",
                source_ref="refs/tags/v0.2.0",
                expected_sha=commit,
                main_ref="refs/remotes/origin/main",
                event_name="push",
                run_attempt="1",
                root=root,
            )
            tag_object = run_git(root, "rev-parse", "refs/tags/v0.2.0")
        self.assertEqual(
            values,
            {
                "source_commit": commit,
                "source_ref": "refs/tags/v0.2.0",
                "tag": "v0.2.0",
                "tag_object": tag_object,
                "version": "0.2.0",
            },
        )

    def test_lightweight_tag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = initialize_tagged_repository(root)
            run_git(root, "tag", "-d", "v0.2.0")
            run_git(root, "tag", "v0.2.0")
            with self.assertRaisesRegex(release.ReleaseError, "annotated"):
                release.validate_release(
                    tag="v0.2.0",
                    source_ref="refs/tags/v0.2.0",
                    expected_sha=commit,
                    main_ref="refs/remotes/origin/main",
                    event_name="push",
                    run_attempt="1",
                    root=root,
                )

    def test_tag_outside_main_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_tagged_repository(root)
            run_git(root, "tag", "-d", "v0.2.0")
            (root / "feature.txt").write_text("not on main\n", encoding="utf-8")
            run_git(root, "add", "feature.txt")
            run_git(root, "commit", "-m", "unmerged release commit")
            run_git(root, "tag", "-a", "v0.2.0", "-m", "Release v0.2.0")
            commit = run_git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(release.ReleaseError, "origin/main"):
                release.validate_release(
                    tag="v0.2.0",
                    source_ref="refs/tags/v0.2.0",
                    expected_sha=commit,
                    main_ref="refs/remotes/origin/main",
                    event_name="push",
                    run_attempt="1",
                    root=root,
                )

    def test_version_mismatch_and_rerun_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = initialize_tagged_repository(root)
            with self.assertRaisesRegex(release.ReleaseError, "version"):
                release.validate_release(
                    tag="v0.2.1",
                    source_ref="refs/tags/v0.2.1",
                    expected_sha=commit,
                    main_ref="refs/remotes/origin/main",
                    event_name="push",
                    run_attempt="1",
                    root=root,
                )
            with self.assertRaisesRegex(release.ReleaseError, "reruns"):
                release.validate_release(
                    tag="v0.2.0",
                    source_ref="refs/tags/v0.2.0",
                    expected_sha=commit,
                    main_ref="refs/remotes/origin/main",
                    event_name="push",
                    run_attempt="2",
                    root=root,
                )

    def test_dirty_checkout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = initialize_tagged_repository(root)
            (root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(release.ReleaseError, "not clean"):
                release.validate_release(
                    tag="v0.2.0",
                    source_ref="refs/tags/v0.2.0",
                    expected_sha=commit,
                    main_ref="refs/remotes/origin/main",
                    event_name="push",
                    run_attempt="1",
                    root=root,
                )


class ReleaseAssetTests(unittest.TestCase):
    def prepare_assets(self, root: Path) -> tuple[Path, str, str]:
        commit = initialize_tagged_repository(root)
        tag_object = run_git(root, "rev-parse", "refs/tags/v0.2.0")
        release_dir = root.parent / f"{root.name}-release"
        epoch = run_git(root, "show", "-s", "--format=%ct", commit)
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": epoch}):
            release.create_source_archive(
                tag="v0.2.0", output_dir=release_dir, root=root
            )
        write_synthetic_wheel(release_dir)
        return release_dir, commit, tag_object

    def test_evidence_and_checksums_are_canonical_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            release_dir, commit, tag_object = self.prepare_assets(root)
            release.finalize_release(
                tag="v0.2.0",
                source_ref="refs/tags/v0.2.0",
                source_commit=commit,
                tag_object=tag_object,
                release_dir=release_dir,
                root=root,
            )
            release.verify_release(
                tag="v0.2.0",
                source_ref="refs/tags/v0.2.0",
                source_commit=commit,
                tag_object=tag_object,
                release_dir=release_dir,
                root=root,
            )
            evidence_bytes = (release_dir / "release-evidence.json").read_bytes()
            evidence = json.loads(evidence_bytes)
            self.assertEqual(
                evidence_bytes,
                release._canonical_json(evidence),
            )
            self.assertEqual(
                [item["kind"] for item in evidence["artifacts"]],
                ["source_archive", "python_wheel"],
            )
            self.assertEqual(evidence["source"]["tag_object"], tag_object)
            checksum_names = [
                line.split("  ", 1)[1]
                for line in (release_dir / "SHA256SUMS")
                .read_text(encoding="ascii")
                .splitlines()
            ]
            self.assertEqual(checksum_names, sorted(checksum_names))
            self.assertNotIn("SHA256SUMS", checksum_names)

    def test_tampered_asset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            release_dir, commit, tag_object = self.prepare_assets(root)
            release.finalize_release(
                tag="v0.2.0",
                source_ref="refs/tags/v0.2.0",
                source_commit=commit,
                tag_object=tag_object,
                release_dir=release_dir,
                root=root,
            )
            wheel = release_dir / release.expected_names("0.2.0")[1]
            wheel.write_bytes(wheel.read_bytes() + b"tampered")
            with self.assertRaises(release.ReleaseError):
                release.verify_release(
                    tag="v0.2.0",
                    source_ref="refs/tags/v0.2.0",
                    source_commit=commit,
                    tag_object=tag_object,
                    release_dir=release_dir,
                    root=root,
                )

    def test_attestation_verify_rejects_forged_evidence_asset_fields(self) -> None:
        mutations = {
            "name": "forged-source.tar.gz",
            "size": 1,
            "sha256": "0" * 64,
        }
        for field, forged_value in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "repo"
                root.mkdir()
                release_dir, commit, tag_object = self.prepare_assets(root)
                release.finalize_release(
                    tag="v0.2.0",
                    source_ref="refs/tags/v0.2.0",
                    source_commit=commit,
                    tag_object=tag_object,
                    release_dir=release_dir,
                    root=root,
                )
                evidence_path = release_dir / "release-evidence.json"
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                evidence["artifacts"][0][field] = forged_value
                rewrite_evidence_and_checksums(release_dir, evidence)
                with self.assertRaisesRegex(
                    release.ReleaseError, "evidence does not match"
                ):
                    release.verify_release(
                        tag="v0.2.0",
                        source_ref="refs/tags/v0.2.0",
                        source_commit=commit,
                        tag_object=tag_object,
                        release_dir=release_dir,
                        root=root,
                    )

    def test_finalize_refuses_extra_files_and_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            release_dir, commit, tag_object = self.prepare_assets(root)
            extra = release_dir / "unexpected.txt"
            extra.write_text("unexpected\n", encoding="utf-8")
            with self.assertRaisesRegex(release.ReleaseError, "unexpected"):
                release.finalize_release(
                    tag="v0.2.0",
                    source_ref="refs/tags/v0.2.0",
                    source_commit=commit,
                    tag_object=tag_object,
                    release_dir=release_dir,
                    root=root,
                )
            extra.unlink()
            release.finalize_release(
                tag="v0.2.0",
                source_ref="refs/tags/v0.2.0",
                source_commit=commit,
                tag_object=tag_object,
                release_dir=release_dir,
                root=root,
            )
            with self.assertRaises(release.ReleaseError):
                release.finalize_release(
                    tag="v0.2.0",
                    source_ref="refs/tags/v0.2.0",
                    source_commit=commit,
                    tag_object=tag_object,
                    release_dir=release_dir,
                    root=root,
                )


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.release_docs = (ROOT / "docs" / "release-process.md").read_text(
            encoding="utf-8"
        )

    def test_release_has_only_a_tag_push_trigger(self) -> None:
        self.assertIn('      - "v*.*.*"', self.workflow)
        self.assertNotIn("workflow_dispatch", self.workflow)
        self.assertNotIn("release:\n", self.workflow.split("jobs:", 1)[0])

    def test_actions_are_exactly_pinned(self) -> None:
        pins = (
            "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            "actions/attest@f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        )
        for pin in pins:
            with self.subTest(pin=pin):
                self.assertIn(pin, self.workflow)

    def test_permissions_are_isolated_across_three_jobs(self) -> None:
        build, remainder = self.workflow.split("  attest:\n", 1)
        attest, publish = remainder.split("  publish:\n", 1)
        self.assertIn("      contents: read", build)
        self.assertNotIn("contents: write", build)
        self.assertNotIn("id-token: write", build)
        self.assertNotIn("attestations: write", build)
        self.assertNotIn("artifact-metadata: write", build)
        self.assertIn("      contents: read", attest)
        self.assertIn("      id-token: write", attest)
        self.assertIn("      attestations: write", attest)
        self.assertIn("      artifact-metadata: write", attest)
        self.assertNotIn("contents: write", attest)
        self.assertNotIn("pip install", attest)
        self.assertNotIn("pip wheel", attest)
        self.assertIn("      contents: write", publish)
        self.assertIn("      attestations: read", publish)
        self.assertNotIn("id-token: write", publish)
        self.assertNotIn("attestations: write", publish)
        self.assertNotIn("actions/checkout@", publish)

    def test_job_dependencies_enforce_build_then_attest_then_publish(self) -> None:
        attest = self.workflow.split("  attest:\n", 1)[1].split("  publish:\n", 1)[0]
        publish = self.workflow.split("  publish:\n", 1)[1]
        self.assertIn("    needs: build", attest)
        self.assertIn("      - build\n      - attest", publish)

    def test_build_uses_hash_locked_tools_without_build_isolation(self) -> None:
        build = self.workflow.split("  attest:\n", 1)[0]
        self.assertIn("--require-hashes", build)
        self.assertIn("requirements/release.txt", build)
        self.assertNotIn('".[dev]"', build)
        install = build[build.index("Install the hash-locked release toolchain") :]
        self.assertIn("--no-deps", install)
        self.assertIn("--no-build-isolation", install)
        package = build[build.index("Build exact source archive and wheel") :]
        self.assertIn("python -m pip wheel .", package)
        self.assertIn("--no-build-isolation", package)

    def test_installed_wheel_versions_must_equal_the_tag_version(self) -> None:
        build = self.workflow.split("  attest:\n", 1)[0]
        wheel_check = build[
            build.index("Verify the wheel in a clean environment") : build.index(
                "Create canonical evidence and checksums"
            )
        ]
        self.assertIn('cli_version="$(\n', wheel_check)
        self.assertIn("import verified_video_pipeline", wheel_check)
        self.assertIn("verified_video_pipeline.__version__", wheel_check)
        self.assertIn("--no-index --no-deps", wheel_check)
        self.assertIn('"${cli_version}" != "${expected_version}"', wheel_check)
        self.assertIn('"${module_version}" != "${expected_version}"', wheel_check)
        self.assertIn("exit 1", wheel_check)

    def test_both_consumers_download_the_same_immutable_artifact_id(self) -> None:
        self.assertIn("overwrite: false", self.workflow)
        self.assertEqual(
            self.workflow.count("artifact-ids: ${{ needs.build.outputs.artifact_id }}"),
            2,
        )
        self.assertEqual(self.workflow.count("merge-multiple: true"), 2)
        self.assertIn(
            "artifact_id: ${{ steps.bundle.outputs['artifact-id'] }}", self.workflow
        )

    def test_publish_reverifies_identity_and_refuses_conflicts(self) -> None:
        required = (
            "GITHUB_RUN_ATTEMPT",
            "sha256sum --check --strict SHA256SUMS",
            "gh attestation verify",
            '--signer-workflow "${GITHUB_REPOSITORY}/.github/workflows/release.yml"',
            '--source-ref "${RELEASE_REF}"',
            '--source-digest "${RELEASE_COMMIT}"',
            '--signer-digest "${RELEASE_COMMIT}"',
            '"repos/${GITHUB_REPOSITORY}/git/ref/tags/${RELEASE_TAG}"',
            '"repos/${GITHUB_REPOSITORY}/git/tags/${RELEASE_TAG_OBJECT}"',
            'gh release view "${RELEASE_TAG}"',
            'gh release create "${RELEASE_TAG}"',
            "--verify-tag",
            "--fail-on-no-commits",
        )
        for contract in required:
            with self.subTest(contract=contract):
                self.assertIn(contract, self.workflow)

    def test_public_tag_control_is_checked_at_publish_time(self) -> None:
        final_step = self.workflow.index(
            "      - name: Revalidate the public tag control and create the release"
        )
        final_block = self.workflow[final_step:]
        required = (
            '"X-GitHub-Api-Version: 2026-03-10"',
            'select(.name == "Immutable release tags")',
            '.target == "tag"',
            '.enforcement == "active"',
            '["refs/tags/v*.*.*"]',
            '["deletion", "update"]',
            'gh release create "${RELEASE_TAG}"',
        )
        for contract in required:
            with self.subTest(contract=contract):
                self.assertIn(contract, final_block)
        self.assertEqual(final_block.count("      - name:"), 1)
        self.assertNotIn("immutable-releases", final_block)
        self.assertNotIn("current_user_can_bypass", final_block)
        self.assertNotIn("bypass_actors", final_block)

    def test_docs_separate_admin_trust_from_workflow_proof(self) -> None:
        self.assertIn("cannot read the administrator-only Immutable", self.release_docs)
        self.assertIn("explicit trust assumptions", self.release_docs)
        self.assertIn("No long-lived administrative token", self.release_docs)

    def test_consumer_docs_verify_all_source_identities(self) -> None:
        for contract in (
            ".source.ref",
            'git cat-file -t "${ref}"',
            'git rev-parse --verify "${ref}"',
            ".source.tag_object",
            'git rev-parse --verify "${ref}^{commit}"',
            ".source.commit",
            "does not prove that the source is",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.release_docs)

    def test_source_is_revalidated_after_tests_before_packaging(self) -> None:
        tests = self.workflow.index("      - name: Run the complete test suite")
        revalidate = self.workflow.index(
            "      - name: Revalidate source and history after tests"
        )
        package = self.workflow.index(
            "      - name: Build exact source archive and wheel"
        )
        self.assertLess(tests, revalidate)
        self.assertLess(revalidate, package)
        block = self.workflow[revalidate:package]
        self.assertIn("scripts/audit_public_tree.py", block)
        self.assertIn("scripts/prepare_release.py check", block)

    def test_full_evidence_verification_precedes_attestation(self) -> None:
        verification = self.workflow.index(
            "      - name: Verify names, sizes, digests, evidence, and checksums"
        )
        attest = self.workflow.index(
            "      - name: Attest every verified release asset"
        )
        self.assertLess(verification, attest)
        verify_block = self.workflow[verification:attest]
        self.assertIn("scripts/prepare_release.py verify", verify_block)
        self.assertIn("sha256sum --check --strict SHA256SUMS", verify_block)
        attest_block = self.workflow[attest : self.workflow.index("  publish:\n")]
        for name in (
            "verified-video-pipeline-",
            "verified_video_pipeline-",
            "release-evidence.json",
            "SHA256SUMS",
        ):
            with self.subTest(name=name):
                self.assertIn(name, attest_block)


if __name__ == "__main__":
    unittest.main()
