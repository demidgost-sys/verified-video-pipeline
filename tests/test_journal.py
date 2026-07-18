from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from verified_video_pipeline.atomic import atomic_write_json, json_sha256, read_json
from verified_video_pipeline.errors import RecoveryRequired
from verified_video_pipeline.journal import StateStore
from verified_video_pipeline.state import Stage, advance, new_state


SOURCE = {
    "path": "source.mp4",
    "sha256": "a" * 64,
    "size": 4,
    "probe": {"duration_seconds": 3.0},
}


def approve_state(state):
    result = advance(state, Stage.PLAN_APPROVED)
    result["approved_plan"] = {
        "path": "approved-plan.json",
        "sha256": "b" * 64,
        "size": 12,
        "reviewer": "test-reviewer",
    }
    return result


class JournalTests(unittest.TestCase):
    def _store(self, root: Path) -> StateStore:
        store = StateStore(root)
        store.initialize(new_state("journal-demo", SOURCE))
        return store

    def test_recover_rolls_forward_after_prepare_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            with self.assertRaisesRegex(RuntimeError, "after journal"):
                store.transact(approve_state, fault="after_prepare")
            self.assertTrue(store.recover())
            self.assertEqual(store.load()["revision"], 1)
            self.assertFalse(store.journal_path.exists())

    def test_recover_finishes_after_apply_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            with self.assertRaisesRegex(RuntimeError, "after state"):
                store.transact(approve_state, fault="after_apply")
            self.assertTrue(store.recover())
            self.assertEqual(store.load()["revision"], 1)

    def test_corrupt_journal_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            with self.assertRaises(RuntimeError):
                store.transact(approve_state, fault="after_prepare")
            record = store.journal_path.read_text(encoding="utf-8").replace(
                "journal-demo", "changed-demo"
            )
            store.journal_path.write_text(record, encoding="utf-8")
            with self.assertRaises(RecoveryRequired):
                store.recover()

    def test_unknown_journal_version_fails_closed_even_with_valid_self_hash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            with self.assertRaises(RuntimeError):
                store.transact(approve_state, fault="after_prepare")
            record = read_json(store.journal_path)
            record.pop("record_sha256")
            record["schema_version"] = 2
            record["record_sha256"] = json_sha256(record)
            atomic_write_json(store.journal_path, record)
            with self.assertRaisesRegex(RecoveryRequired, "schema version"):
                store.recover()


if __name__ == "__main__":
    unittest.main()
