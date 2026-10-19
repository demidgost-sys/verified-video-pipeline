from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_release import (
    ROOT,
    initialize_tagged_repository,
    release,
    write_synthetic_wheel,
)


spec = importlib.util.spec_from_file_location(
    "release_checks", ROOT / "scripts/release_checks.py"
)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
COMMIT = "a" * 40


def run(path, number, **changes):
    return {
        "id": number,
        "path": path,
        "head_sha": COMMIT,
        "event": "push",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        **changes,
    }


class ReleaseGateTests(unittest.TestCase):
    def test_exact_main_ci_and_codeql_are_required(self):
        good = [run(path, i + 1) for i, path in enumerate(checks.WORKFLOWS)]
        self.assertEqual(len(checks.select_checks(good, COMMIT)), 2)
        for changes in (
            {"head_sha": "b" * 40},
            {"head_branch": "feature"},
            {"event": "pull_request"},
            {"conclusion": "failure"},
            {"status": "in_progress"},
            {"id": True},
        ):
            bad = [good[0], {**good[1], **changes}]
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                checks.select_checks(bad, COMMIT)
        with self.assertRaises(ValueError):
            checks.select_checks(
                [*good, run(checks.WORKFLOWS[0], 3, conclusion="failure")], COMMIT
            )

    def test_clean_rebuild_comparison_rejects_different_bytes(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ):
            # Synthetic repositories own their timestamps; the caller's release
            # epoch must be restored when this test finishes, not applied here.
            os.environ.pop("SOURCE_DATE_EPOCH", None)
            root = Path(directory)
            initialize_tagged_repository(root)
            assets = root / "dist"
            release.create_source_archive(tag="v0.2.0", output_dir=assets, root=root)
            candidate = write_synthetic_wheel(assets)
            original = subprocess.run
            for identical in (True, False):

                def fake_run(argv, **kwargs):
                    if argv[0] == "git":
                        return original(argv, **kwargs)
                    self.assertIn("--no-index", argv)
                    self.assertIn("--no-build-isolation", argv)
                    self.assertEqual(kwargs["timeout"], 180)
                    output = Path(argv[argv.index("--wheel-dir") + 1])
                    output.mkdir()
                    (output / candidate.name).write_bytes(
                        candidate.read_bytes() if identical else b"different"
                    )
                    return subprocess.CompletedProcess(argv, 0, b"", b"")

                with mock.patch.object(release.subprocess, "run", side_effect=fake_run):
                    if identical:
                        self.assertEqual(
                            len(
                                release.reproduce_wheel(
                                    tag="v0.2.0", release_dir=assets, root=root
                                )
                            ),
                            64,
                        )
                    else:
                        with self.assertRaisesRegex(release.ReleaseError, "differ"):
                            release.reproduce_wheel(
                                tag="v0.2.0", release_dir=assets, root=root
                            )
