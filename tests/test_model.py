"""Bounded lifecycle/WAL reference model with deterministic fault sequences."""

from __future__ import annotations

import os
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from synthetic_contracts import engine_record, loudness_record
from verified_video_pipeline.atomic import atomic_write_json
from verified_video_pipeline.errors import ContractError, RecoveryRequired
from verified_video_pipeline.journal import StateStore
from verified_video_pipeline.state import Stage, advance, new_state


# Deliberately independent of NEXT_STAGE and validate_transition.
STAGES = ("REGISTERED", "PLAN_APPROVED", "MASTER_READY", "QA_PASSED", "READY")
NOW = "2026-01-01T00:00:00+00:00"
SOURCE = {
    "path": "source.mp4",
    "sha256": "a" * 64,
    "size": 4,
    "probe": {"duration_seconds": 3.0},
}
PLAN = {
    "path": "approved-plan.json",
    "sha256": "b" * 64,
    "size": 8,
    "reviewer": "synthetic-human",
}
MASTER = {
    "path": "master.mp4",
    "sha256": "c" * 64,
    "size": 16,
    "probe": SOURCE["probe"],
    "profile": "h264-aac-web",
    "source_sha256": SOURCE["sha256"],
    "plan_sha256": PLAN["sha256"],
    "engine": engine_record(),
}
QA = {
    "status": "PASS",
    "checks": ["synthetic-model-check"],
    "checked_at": NOW,
    "probe": SOURCE["probe"],
    "loudness": loudness_record(),
    "master_sha256": MASTER["sha256"],
    "engine_sha256": MASTER["engine"]["sha256"],
}
FIELDS = {
    1: ("approved_plan", PLAN),
    2: ("master", MASTER),
    3: ("qa", QA),
    4: (
        "release_manifest",
        {"path": "release-manifest.json", "sha256": "d" * 64, "size": 32},
    ),
}


def next_snapshot(state, target):
    result = deepcopy(state)
    result.update(stage=STAGES[target], revision=state["revision"] + 1, updated_at=NOW)
    key, value = FIELDS[target]
    result[key] = deepcopy(value)
    return result


def implementation_mutator(target):
    def mutate(state):
        result = advance(state, Stage(STAGES[target]))
        if target in FIELDS:
            key, value = FIELDS[target]
            result[key] = deepcopy(value)
        return result

    return mutate


class ReferenceModel:
    def __init__(self):
        self.state = {
            "schema_version": 2,
            "revision": 0,
            "content_id": "synthetic-model",
            "stage": "REGISTERED",
            "source": deepcopy(SOURCE),
            "created_at": NOW,
            "updated_at": NOW,
        }
        self.pending = None
        self.corrupt = False


class LifecycleModelTests(unittest.TestCase):
    def run_sequence(self, seed, steps):
        rng = random.Random(seed)
        history = []
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch("verified_video_pipeline.state.utc_now", return_value=NOW),
        ):
            root = Path(directory)
            model = ReferenceModel()
            store = StateStore(root)
            store.initialize(new_state("synthetic-model", SOURCE))
            replay = None
            episode = 0
            for index in range(steps):
                operation = rng.choices(
                    [
                        "advance",
                        "recover",
                        "restart",
                        "replay",
                        "corrupt",
                        "external",
                        "reset",
                    ],
                    [42, 24, 6, 8, 7, 5, 8],
                )[0]
                current = STAGES.index(model.state["stage"])
                target = min(4, current + 1) if rng.random() < 0.7 else rng.randrange(5)
                fault = rng.choice([None, "after_prepare", "after_apply"])
                history.append((operation, target, fault))
                try:
                    if operation == "advance":
                        if model.pending is not None:
                            with self.assertRaises(RecoveryRequired):
                                store.transact(
                                    implementation_mutator(target), fault=fault
                                )
                        elif target != current + 1:
                            with self.assertRaises(ContractError):
                                store.transact(
                                    implementation_mutator(target), fault=fault
                                )
                        else:
                            before = deepcopy(model.state)
                            after = next_snapshot(before, target)
                            if fault is None:
                                store.transact(implementation_mutator(target))
                            else:
                                with self.assertRaises(RuntimeError):
                                    store.transact(
                                        implementation_mutator(target), fault=fault
                                    )
                                model.pending = (deepcopy(before), deepcopy(after))
                                replay = (
                                    store.journal_path.read_bytes(),
                                    deepcopy(model.pending),
                                )
                            if fault != "after_prepare":
                                model.state = deepcopy(after)
                    elif operation == "recover":
                        if model.pending is None:
                            self.assertFalse(store.recover())
                        elif model.corrupt or model.state not in model.pending:
                            original = store.journal_path.read_bytes()
                            with self.assertRaises(RecoveryRequired):
                                store.recover()
                            self.assertEqual(store.journal_path.read_bytes(), original)
                        else:
                            self.assertTrue(store.recover())
                            model.state = deepcopy(model.pending[1])
                            model.pending = None
                    elif operation == "restart":
                        store = StateStore(store.root)
                    elif operation == "replay" and model.pending is None and replay:
                        store.journal_path.write_bytes(replay[0])
                        model.pending = deepcopy(replay[1])
                        model.corrupt = False
                    elif operation == "corrupt" and model.pending is not None:
                        store.journal_path.write_bytes(
                            store.journal_path.read_bytes() + b" "
                        )
                        # Whitespace alone is legal JSON, so change the self-hash.
                        raw = store.journal_path.read_text()
                        store.journal_path.write_text(
                            raw.replace('"record_sha256":"', '"record_sha256":"0', 1)
                        )
                        model.corrupt = True
                    elif operation == "external" and model.pending is not None:
                        model.state["revision"] += 7
                        atomic_write_json(store.state_path, model.state)
                    elif operation == "reset":
                        episode += 1
                        episode_root = root / str(episode)
                        episode_root.mkdir()
                        store = StateStore(episode_root)
                        model = ReferenceModel()
                        store.initialize(new_state("synthetic-model", SOURCE))
                        # Replay intentionally survives a reset to test cross-episode journals.
                    self.assertEqual(store.load(), model.state)
                    self.assertEqual(
                        store.has_pending_recovery(), model.pending is not None
                    )
                except Exception as exc:
                    # Every prefix has been checked. This is the shortest failing
                    # prefix for this seed; replay does not need previous seeds.
                    self.fail(
                        f"seed={seed} first_failing_prefix={index + 1}; "
                        f"VVP_MODEL_SEED={seed} VVP_MODEL_STEPS={index + 1}; "
                        f"operations={history!r}; cause={exc!r}"
                    )

    def test_seeded_lifecycle_and_recovery(self):
        profile = os.environ.get("VVP_MODEL_PROFILE", "pr")
        self.assertIn(profile, {"pr", "deep"})
        count, steps = (32, 48) if profile == "pr" else (512, 128)
        if "VVP_MODEL_STEPS" in os.environ:
            steps = int(os.environ["VVP_MODEL_STEPS"])
        self.assertTrue(1 <= steps <= 256)
        if "VVP_MODEL_SEED" in os.environ:
            seed = int(os.environ["VVP_MODEL_SEED"])
            self.assertTrue(0 <= seed < 2**32)
            seeds = (seed,)
        else:
            seeds = range(count)
        for seed in seeds:
            self.run_sequence(seed, steps)
