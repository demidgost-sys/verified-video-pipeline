from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from verified_video_pipeline.atomic import atomic_write_json, json_sha256
from verified_video_pipeline.errors import IntegrityError, PipelineError
from verified_video_pipeline.media import PROFILE, generate_synthetic_video
from verified_video_pipeline.pipeline import (
    approve_plan,
    build,
    create_manifest,
    initialize,
    recover,
    run_qa,
    status,
)


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required"
)
class V02IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template_directory = tempfile.TemporaryDirectory()
        cls.template = Path(cls.template_directory.name) / "project"
        cls.template.mkdir()
        generate_synthetic_video(cls.template / "source.mp4", duration=2)
        atomic_write_json(
            cls.template / "input-plan.json",
            {
                "schema_version": 1,
                "profile": PROFILE,
                "trim": {"start_seconds": 0.1, "end_seconds": 1.8},
            },
        )
        initialize(cls.template, cls.template / "source.mp4", "synthetic-v02")
        approve_plan(cls.template, cls.template / "input-plan.json", "synthetic-human")
        build(cls.template)

    @classmethod
    def tearDownClass(cls):
        cls.template_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "project"
        shutil.copytree(self.template, self.root)

    def drift(self):
        state = json.loads((self.root / "project.json").read_text())
        changed = deepcopy(state["master"]["engine"])
        changed["required"]["canary_sha256"] = "0" * 64
        changed["sha256"] = json_sha256(changed["required"])
        return mock.patch(
            "verified_video_pipeline.pipeline.capture_engine", return_value=changed
        )

    def test_drift_blocks_qa_and_idempotent_build(self):
        before = (self.root / "project.json").read_bytes()
        for operation in (build, run_qa):
            with self.drift(), self.assertRaisesRegex(IntegrityError, "drift"):
                operation(self.root)
            self.assertEqual((self.root / "project.json").read_bytes(), before)

    def test_engine_change_during_qa_does_not_commit_measurements(self):
        before = (self.root / "project.json").read_bytes()
        engine = json.loads(before)["master"]["engine"]
        changed = deepcopy(engine)
        changed["required"]["canary_sha256"] = "0" * 64
        changed["sha256"] = json_sha256(changed["required"])
        with mock.patch(
            "verified_video_pipeline.pipeline.capture_engine",
            side_effect=[engine, changed],
        ):
            with self.assertRaises(IntegrityError):
                run_qa(self.root)
        self.assertEqual((self.root / "project.json").read_bytes(), before)

    def test_drift_blocks_manifest_and_withdraws_ready_without_changing_evidence(self):
        run_qa(self.root)
        with self.drift(), self.assertRaises(IntegrityError):
            create_manifest(self.root)
        self.assertFalse((self.root / "release-manifest.json").exists())
        create_manifest(self.root)
        manifest_bytes = (self.root / "release-manifest.json").read_bytes()
        with self.drift():
            report = status(self.root, verify=True)
        self.assertFalse(report["ready"])
        self.assertIn("ENGINE_CONTRACT_DRIFT", report["blockers"])
        self.assertEqual(
            (self.root / "release-manifest.json").read_bytes(), manifest_bytes
        )
        self.assertFalse(status(self.root)["ready"])

    def test_loudness_failure_and_master_mutation_cannot_advance_qa(self):
        with mock.patch(
            "verified_video_pipeline.pipeline.measure_loudness",
            side_effect=PipelineError("out of tolerance"),
        ):
            with self.assertRaises(PipelineError):
                run_qa(self.root)
        self.assertEqual(status(self.root)["stage"], "MASTER_READY")
        from verified_video_pipeline.loudness import measure_loudness

        def mutate(path):
            evidence = measure_loudness(path)
            data = path.read_bytes()
            path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            return evidence

        with mock.patch(
            "verified_video_pipeline.pipeline.measure_loudness", side_effect=mutate
        ):
            with self.assertRaises(IntegrityError):
                run_qa(self.root)
        self.assertEqual(status(self.root)["stage"], "MASTER_READY")

    def test_crash_receipt_cannot_promote_under_changed_engine(self):
        # Create a fresh approved workspace with real synthetic bytes.
        fresh = Path(self.directory.name) / "recovery"
        shutil.copytree(self.template, fresh)
        # Start instead from the pre-build state captured through a fresh lifecycle.
        clean = Path(self.directory.name) / "fresh"
        clean.mkdir()
        shutil.copy2(fresh / "source.mp4", clean / "source.mp4")
        initialize(clean, clean / "source.mp4", "synthetic-recovery")
        approve_plan(clean, fresh / "input-plan.json", "synthetic-human")
        with mock.patch(
            "verified_video_pipeline.pipeline._install_no_clobber",
            side_effect=RuntimeError("crash"),
        ):
            with self.assertRaises(RuntimeError):
                build(clean)
        receipt = (clean / "build-receipt.json").read_bytes()
        original_root = self.root
        self.root = clean
        changed = json.loads(receipt)["master"]["engine"]
        changed["required"]["canary_sha256"] = "0" * 64
        changed["sha256"] = json_sha256(changed["required"])
        with mock.patch(
            "verified_video_pipeline.pipeline.capture_engine", return_value=changed
        ):
            with self.assertRaises(IntegrityError):
                recover(clean)
        self.root = original_root
        self.assertFalse((clean / "master.mp4").exists())
        self.assertEqual((clean / "build-receipt.json").read_bytes(), receipt)
        self.assertTrue((clean / ".master.staged.mp4").exists())
        self.assertEqual(recover(clean)["stage"], "MASTER_READY")

    def test_legacy_ready_remains_readable_but_cannot_claim_v02_verification(self):
        run_qa(self.root)
        create_manifest(self.root)
        state_path = self.root / "project.json"
        state = json.loads(state_path.read_text())
        state["schema_version"] = 1
        state["master"].pop("engine")
        for key in ("engine_sha256", "master_sha256", "loudness"):
            state["qa"].pop(key)
        atomic_write_json(state_path, state)
        report = status(self.root, verify=True)
        self.assertFalse(report["ready"])
        self.assertIn("LEGACY_EVIDENCE_REBUILD_REQUIRED", report["blockers"])

    def test_measurements_and_engine_are_bound_to_v02_manifest(self):
        run_qa(self.root)
        create_manifest(self.root)
        manifest = json.loads((self.root / "release-manifest.json").read_text())
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(
            manifest["qa"]["engine_sha256"], manifest["artifact"]["engine"]["sha256"]
        )
        self.assertEqual(
            manifest["qa"]["master_sha256"], manifest["artifact"]["sha256"]
        )
        self.assertLessEqual(abs(manifest["qa"]["loudness"]["integrated_lufs"] + 16), 1)
        self.assertTrue(status(self.root, verify=True)["ready"])
