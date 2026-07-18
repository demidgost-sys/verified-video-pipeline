from __future__ import annotations

import unittest
from copy import deepcopy

from verified_video_pipeline.errors import ContractError
from verified_video_pipeline.state import (
    Stage,
    advance,
    new_state,
    validate_content_id,
    validate_state,
)


PROBE = {"duration_seconds": 3.0}
SOURCE = {
    "path": "source.mp4",
    "sha256": "a" * 64,
    "size": 4,
    "probe": PROBE,
}
APPROVED_PLAN = {
    "path": "approved-plan.json",
    "sha256": "b" * 64,
    "size": 12,
    "reviewer": "test-reviewer",
}
MASTER = {
    "path": "master.mp4",
    "sha256": "c" * 64,
    "size": 3,
    "profile": "h264-aac-web",
    "probe": PROBE,
    "source_sha256": SOURCE["sha256"],
    "plan_sha256": APPROVED_PLAN["sha256"],
}
QA = {
    "status": "PASS",
    "checked_at": "2026-07-18T12:00:00+00:00",
    "checks": ["strict full decode completed without errors"],
    "probe": PROBE,
}
RELEASE_MANIFEST = {
    "path": "release-manifest.json",
    "sha256": "d" * 64,
    "size": 10,
}


def approved_state() -> dict:
    state = advance(new_state("lesson-01", SOURCE), Stage.PLAN_APPROVED)
    state["approved_plan"] = deepcopy(APPROVED_PLAN)
    validate_state(state)
    return state


def master_state() -> dict:
    state = advance(approved_state(), Stage.MASTER_READY)
    state["master"] = deepcopy(MASTER)
    validate_state(state)
    return state


def qa_state() -> dict:
    state = advance(master_state(), Stage.QA_PASSED)
    state["qa"] = deepcopy(QA)
    validate_state(state)
    return state


class StateTests(unittest.TestCase):
    def test_content_id_contract(self) -> None:
        self.assertEqual(validate_content_id("lesson-01"), "lesson-01")
        for invalid in ("Upper", "../escape", "x" * 65, "-prefix"):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                validate_content_id(invalid)

    def test_state_machine_rejects_skipped_gate(self) -> None:
        state = new_state("lesson-01", SOURCE)
        with self.assertRaises(ContractError):
            advance(state, Stage.MASTER_READY)

    def test_boolean_schema_and_revision_are_rejected(self) -> None:
        for field in ("schema_version", "revision"):
            with self.subTest(field=field):
                state = new_state("lesson-01", SOURCE)
                state[field] = True
                with self.assertRaises(ContractError):
                    validate_state(state)

    def test_registered_source_requires_finite_positive_probe_duration(self) -> None:
        invalid_probes = (
            None,
            {},
            {"duration_seconds": True},
            {"duration_seconds": 0},
            {"duration_seconds": float("inf")},
            {"duration_seconds": float("nan")},
        )
        for probe in invalid_probes:
            with self.subTest(probe=probe):
                source = deepcopy(SOURCE)
                if probe is None:
                    source.pop("probe")
                else:
                    source["probe"] = probe
                with self.assertRaisesRegex(ContractError, "source probe"):
                    new_state("lesson-01", source)

    def test_approved_plan_requires_artifact_identity_and_reviewer(self) -> None:
        state = approved_state()
        for field in ("path", "sha256", "size", "reviewer"):
            with self.subTest(field=field):
                invalid = deepcopy(state)
                invalid["approved_plan"].pop(field)
                with self.assertRaises(ContractError):
                    validate_state(invalid)

    def test_master_requires_manifest_fields_and_upstream_bindings(self) -> None:
        state = master_state()
        for field in ("profile", "probe", "source_sha256", "plan_sha256"):
            with self.subTest(field=field):
                invalid = deepcopy(state)
                invalid["master"].pop(field)
                with self.assertRaises(ContractError):
                    validate_state(invalid)

        for field in ("source_sha256", "plan_sha256"):
            with self.subTest(binding=field):
                invalid = deepcopy(state)
                invalid["master"][field] = "f" * 64
                with self.assertRaisesRegex(ContractError, "different"):
                    validate_state(invalid)

    def test_qa_requires_manifest_ready_evidence_shape(self) -> None:
        state = qa_state()
        for field in ("checked_at", "checks", "probe"):
            with self.subTest(field=field):
                invalid = deepcopy(state)
                invalid["qa"].pop(field)
                with self.assertRaises(ContractError):
                    validate_state(invalid)

        invalid = deepcopy(state)
        invalid["qa"]["checks"] = [""]
        with self.assertRaisesRegex(ContractError, "checks"):
            validate_state(invalid)

    def test_ready_requires_release_manifest_identity(self) -> None:
        state = advance(qa_state(), Stage.READY)
        state["release_manifest"] = deepcopy(RELEASE_MANIFEST)
        validate_state(state)
        state["release_manifest"].pop("size")
        with self.assertRaisesRegex(ContractError, "release manifest"):
            validate_state(state)


if __name__ == "__main__":
    unittest.main()
