from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator, FormatChecker

from verified_video_pipeline.atomic import (
    atomic_write_json,
    canonical_json_bytes,
    json_sha256,
)
from verified_video_pipeline.errors import (
    ContractError,
    IntegrityError,
    PipelineError,
    RecoveryRequired,
)
from verified_video_pipeline.media import (
    PROFILE,
    generate_synthetic_video,
    probe as real_probe,
)
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


FFMPEG_AVAILABLE = (
    shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
)


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg and ffprobe are required")
class PipelineIntegrationTests(unittest.TestCase):
    def _approved_project(self, root: Path) -> None:
        source = root / "source.mp4"
        plan = root / "plan.json"
        generate_synthetic_video(source, duration=1.5)
        atomic_write_json(
            plan,
            {
                "schema_version": 1,
                "profile": PROFILE,
                "trim": {"start_seconds": 0.1, "end_seconds": 1.3},
            },
        )
        initialize(root, source, "integration-demo")
        approve_plan(root, plan, "test-reviewer")

    def test_synthetic_end_to_end_is_ready_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "demo"
            report = run_demo(root)
            self.assertTrue(report["ready"])
            self.assertEqual(report["stage"], "READY")
            manifest = json.loads((root / "release-manifest.json").read_text())
            serialized = json.dumps(manifest)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("oauth", serialized.lower())
            self.assertEqual(
                manifest["provenance"]["public_repository_fixture_policy"],
                "synthetic-only",
            )
            self.assertNotIn("content_id", manifest)
            self.assertNotIn("filename", manifest["source"])
            self.assertNotIn("reviewer", manifest["approved_plan"])
            self.assertEqual(manifest["approved_plan"]["gate"], "human-approved")
            schema = json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "schemas"
                    / "release-manifest.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(
                manifest
            )

    def test_source_mutation_after_approval_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            source = root / "source.mp4"
            data = source.read_bytes()
            source.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            with self.assertRaises(IntegrityError):
                build(root)
            self.assertEqual(status(root)["stage"], "PLAN_APPROVED")

    def test_build_cannot_parse_bytes_outside_approved_plan_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            from verified_video_pipeline import pipeline as pipeline_module

            original_reader = pipeline_module.stable_read_bytes
            alternate = canonical_json_bytes(
                {
                    "schema_version": 1,
                    "profile": PROFILE,
                    "trim": {"start_seconds": 0.0, "end_seconds": 1.4},
                }
            )

            def substituted_read(path: Path, **kwargs):
                if path.name == "approved-plan.json":
                    return alternate, {
                        "sha256": hashlib.sha256(alternate).hexdigest(),
                        "size": len(alternate),
                    }
                return original_reader(path, **kwargs)

            with mock.patch(
                "verified_video_pipeline.pipeline.stable_read_bytes",
                side_effect=substituted_read,
            ):
                with self.assertRaisesRegex(IntegrityError, "approved plan"):
                    build(root)
            self.assertFalse((root / "master.mp4").exists())
            self.assertFalse((root / "build-receipt.json").exists())

    def test_trim_cannot_extend_beyond_registered_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            plan = root / "plan.json"
            generate_synthetic_video(source, duration=1.5)
            atomic_write_json(
                plan,
                {
                    "schema_version": 1,
                    "profile": PROFILE,
                    "trim": {"start_seconds": 0.5, "end_seconds": 99.0},
                },
            )
            initialize(root, source, "invalid-trim-demo")
            with self.assertRaisesRegex(ContractError, "exceeds"):
                approve_plan(root, plan, "test-reviewer")
            self.assertEqual(status(root)["stage"], "REGISTERED")

    def test_dangling_staging_symlink_cannot_escape_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "project"
            root.mkdir()
            self._approved_project(root)
            escaped = parent / "escaped.mp4"
            staged = root / ".master.staged.mp4"
            staged.symlink_to(escaped)
            with self.assertRaises(ContractError):
                build(root)
            self.assertFalse(escaped.exists())
            self.assertTrue(staged.is_symlink())
            report = status(root)
            self.assertEqual(report["stage"], "PLAN_APPROVED")
            self.assertIn("UNREGISTERED_STAGING", report["blockers"])
            self.assertEqual(report["next_action"], "operator-review")

    def test_status_blocks_regular_orphan_staging_without_modifying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            staged = root / ".master.staged.mp4"
            original = b"unregistered staging bytes"
            staged.write_bytes(original)
            state_before = (root / "project.json").read_bytes()

            report = status(root)

            self.assertEqual(report["stage"], "PLAN_APPROVED")
            self.assertIn("UNREGISTERED_STAGING", report["blockers"])
            self.assertEqual(report["next_action"], "operator-review")
            self.assertEqual(staged.read_bytes(), original)
            self.assertEqual((root / "project.json").read_bytes(), state_before)

    def test_existing_master_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            master = root / "master.mp4"
            original = b"unrelated master bytes"
            master.write_bytes(original)
            with self.assertRaises(RecoveryRequired):
                build(root)
            self.assertEqual(master.read_bytes(), original)
            report = status(root)
            self.assertEqual(report["stage"], "PLAN_APPROVED")
            self.assertIn("UNREGISTERED_MASTER", report["blockers"])
            self.assertEqual(report["next_action"], "operator-review")

    def test_status_blocks_dangling_unregistered_master_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "project"
            root.mkdir()
            self._approved_project(root)
            missing_target = parent / "missing-master.mp4"
            master = root / "master.mp4"
            master.symlink_to(missing_target)
            state_before = (root / "project.json").read_bytes()

            report = status(root)

            self.assertEqual(report["stage"], "PLAN_APPROVED")
            self.assertIn("UNREGISTERED_MASTER", report["blockers"])
            self.assertEqual(report["next_action"], "operator-review")
            self.assertTrue(master.is_symlink())
            self.assertEqual(master.readlink(), missing_target)
            self.assertEqual((root / "project.json").read_bytes(), state_before)

    def test_init_rejects_every_reserved_source_path_without_modifying_it(self) -> None:
        reserved = {
            ".vvp.lock",
            "project.json",
            "journal.json",
            "approved-plan.json",
            "master.mp4",
            ".master.staged.mp4",
            "build-receipt.json",
            "release-manifest.json",
        }
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            for index, name in enumerate(sorted(reserved)):
                with self.subTest(name=name):
                    root = parent / f"case-{index}"
                    root.mkdir()
                    source = root / name
                    original = b"immutable source bytes"
                    source.write_bytes(original)
                    with self.assertRaisesRegex(ContractError, "reserved runtime path"):
                        initialize(root, source, "reserved-name-demo")
                    self.assertEqual(source.read_bytes(), original)

    def test_init_rejects_preexisting_managed_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source bytes")
            staged = root / ".master.staged.mp4"
            staged.write_bytes(b"unrelated bytes")
            with self.assertRaisesRegex(ContractError, "already exists"):
                initialize(root, source, "occupied-namespace-demo")
            self.assertEqual(staged.read_bytes(), b"unrelated bytes")

    def test_init_rejects_source_hardlinked_to_existing_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mp4"
            original = b"immutable source bytes"
            source.write_bytes(original)
            (root / ".vvp.lock").hardlink_to(source)
            with self.assertRaisesRegex(ContractError, "aliases"):
                initialize(root, source, "hardlink-alias-demo")
            self.assertEqual(source.read_bytes(), original)

    def test_build_receipt_recovers_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            with mock.patch(
                "verified_video_pipeline.pipeline._install_no_clobber",
                side_effect=RuntimeError("simulated process death"),
            ):
                with self.assertRaisesRegex(RuntimeError, "process death"):
                    build(root)
            self.assertTrue((root / "build-receipt.json").exists())
            report = status(root)
            self.assertEqual(report["blockers"], ["RECOVERY_REQUIRED"])
            self.assertEqual(report["next_action"], "recover")
            recovered = recover(root)
            self.assertEqual(recovered["stage"], "MASTER_READY")
            self.assertTrue((root / "master.mp4").is_file())
            self.assertFalse((root / "build-receipt.json").exists())

    def test_status_does_not_flag_registered_master_across_later_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            build(root)

            report = status(root)
            self.assertEqual(report["stage"], "MASTER_READY")
            self.assertNotIn("UNREGISTERED_MASTER", report["blockers"])
            self.assertEqual(report["next_action"], "qa")

            run_qa(root)
            report = status(root)
            self.assertEqual(report["stage"], "QA_PASSED")
            self.assertNotIn("UNREGISTERED_MASTER", report["blockers"])
            self.assertEqual(report["next_action"], "manifest")

            create_manifest(root)
            report = status(root)
            self.assertEqual(report["stage"], "READY")
            self.assertNotIn("UNREGISTERED_MASTER", report["blockers"])
            self.assertEqual(report["next_action"], "none")

    def test_corrupt_post_commit_build_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            with mock.patch(
                "verified_video_pipeline.pipeline.atomic_unlink",
                side_effect=RuntimeError("simulated death before receipt cleanup"),
            ):
                with self.assertRaisesRegex(RuntimeError, "receipt cleanup"):
                    build(root)
            self.assertEqual(status(root)["stage"], "MASTER_READY")
            receipt = root / "build-receipt.json"
            receipt.write_text(
                receipt.read_text(encoding="utf-8").replace(
                    '"schema_version":1', '"schema_version":2'
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RecoveryRequired):
                recover(root)

    def test_post_commit_staging_symlink_keeps_receipt_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "project"
            root.mkdir()
            self._approved_project(root)
            with mock.patch(
                "verified_video_pipeline.pipeline.atomic_unlink",
                side_effect=RuntimeError("simulated death before receipt cleanup"),
            ):
                with self.assertRaises(RuntimeError):
                    build(root)
            receipt = root / "build-receipt.json"
            staged = root / ".master.staged.mp4"
            staged.symlink_to(parent / "outside.mp4")
            for _ in range(2):
                with self.assertRaises(RecoveryRequired):
                    recover(root)
                self.assertTrue(receipt.is_file())
                self.assertTrue(staged.is_symlink())

    def test_master_mutation_after_qa_blocks_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            build(root)
            run_qa(root)
            master = root / "master.mp4"
            data = master.read_bytes()
            master.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            with self.assertRaises(IntegrityError):
                create_manifest(root)
            self.assertEqual(status(root)["stage"], "QA_PASSED")

    def test_qa_rejects_probe_duration_that_disagrees_with_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            build(root)
            fake_probe = {
                "duration_seconds": 99.0,
                "format_name": "mov,mp4",
                "streams": [],
            }
            with mock.patch(
                "verified_video_pipeline.pipeline.technical_checks",
                return_value=(fake_probe, ["synthetic check"]),
            ):
                with self.assertRaisesRegex(PipelineError, "duration"):
                    run_qa(root)
            self.assertEqual(status(root)["stage"], "MASTER_READY")

    def test_status_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            before = (root / "project.json").read_bytes()
            status(root, verify=True)
            self.assertEqual((root / "project.json").read_bytes(), before)

    def test_status_blocks_orphans_before_plan_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            generate_synthetic_video(source, duration=1.0)
            initialize(root, source, "integration-demo")
            master = root / "master.mp4"
            staged = root / ".master.staged.mp4"
            master_bytes = b"unregistered master bytes"
            staged_bytes = b"unregistered staging bytes"
            master.write_bytes(master_bytes)
            staged.write_bytes(staged_bytes)
            state_before = (root / "project.json").read_bytes()

            report = status(root)

            self.assertEqual(report["stage"], "REGISTERED")
            self.assertEqual(
                report["blockers"],
                ["UNREGISTERED_MASTER", "UNREGISTERED_STAGING"],
            )
            self.assertEqual(report["next_action"], "operator-review")
            self.assertEqual(master.read_bytes(), master_bytes)
            self.assertEqual(staged.read_bytes(), staged_bytes)
            self.assertEqual((root / "project.json").read_bytes(), state_before)

    def test_init_rejects_source_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "project"
            root.mkdir()
            outside = parent / "outside.mp4"
            outside.write_bytes(b"not media")
            with self.assertRaises(ContractError):
                initialize(root, outside, "unsafe-demo")

    def test_init_rejects_source_replacement_between_probe_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            replacement = root / "replacement.mp4"
            generate_synthetic_video(source, duration=1.0)
            generate_synthetic_video(replacement, duration=2.0)

            def replace_after_probe(path: Path):
                evidence = real_probe(path)
                replacement.replace(path)
                return evidence

            with mock.patch(
                "verified_video_pipeline.pipeline.probe",
                side_effect=replace_after_probe,
            ):
                with self.assertRaisesRegex(IntegrityError, "while probing"):
                    initialize(root, source, "probe-race-demo")
            self.assertFalse((root / "project.json").exists())

    def test_init_rejects_intermediate_workspace_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            source = real / "source.mp4"
            generate_synthetic_video(source, duration=1.0)
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ContractError, "symlink"):
                initialize(root, alias / "source.mp4", "symlink-demo")
            self.assertFalse((root / "project.json").exists())

    def test_init_rechecks_path_only_after_project_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            replacement = root / "replacement.mp4"
            generate_synthetic_video(source, duration=1.0)
            generate_synthetic_video(replacement, duration=2.0)

            class RetargetOnEnter:
                def __enter__(self):
                    source.unlink()
                    source.symlink_to(replacement)

                def __exit__(self, exc_type, exc, traceback):
                    return False

            with mock.patch(
                "verified_video_pipeline.pipeline.project_lease",
                return_value=RetargetOnEnter(),
            ):
                with self.assertRaises(ContractError):
                    initialize(root, source, "lease-race-demo")
            self.assertFalse((root / "project.json").exists())

    def test_unknown_build_receipt_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._approved_project(root)
            with mock.patch(
                "verified_video_pipeline.pipeline.atomic_unlink",
                side_effect=RuntimeError("simulated death before receipt cleanup"),
            ):
                with self.assertRaises(RuntimeError):
                    build(root)
            receipt_path = root / "build-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt.pop("record_sha256")
            receipt["schema_version"] = 2
            receipt["record_sha256"] = json_sha256(receipt)
            atomic_write_json(receipt_path, receipt)
            with self.assertRaisesRegex(RecoveryRequired, "schema version"):
                recover(root)

    def test_ready_integrity_blocker_requires_operator_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "demo"
            run_demo(root)
            master = root / "master.mp4"
            data = master.read_bytes()
            master.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            report = status(root, verify=True)
            self.assertFalse(report["ready"])
            self.assertIn("HASH_MISMATCH_MASTER", report["blockers"])
            self.assertEqual(report["next_action"], "operator-review")


if __name__ == "__main__":
    unittest.main()
