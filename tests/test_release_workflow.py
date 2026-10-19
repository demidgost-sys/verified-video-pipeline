"""Execute the publish identity gate, rather than only the build-side verifier."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from test_release import (
    ROOT,
    initialize_tagged_repository,
    release,
    rewrite_evidence_and_checksums,
    run_git,
    write_synthetic_wheel,
)


def publish_job():
    return (
        (ROOT / ".github/workflows/release.yml").read_text().split("  publish:\n", 1)[1]
    )


def publish_script(name):
    job = publish_job()
    step = job.split(f"      - name: {name}\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


class PublishSourceContractTests(unittest.TestCase):
    def test_notes_helper_is_materialized_from_exact_build_commit_before_use(self):
        job = publish_job()
        helper = job.index("python3 publish-source/scripts/release_checks.py")
        prefix = job[:helper]
        self.assertIn("uses: actions/checkout@", prefix)
        checkout = prefix[prefix.rindex("uses: actions/checkout@") :].split(
            "\n      - name:", 1
        )[0]
        self.assertIn("ref: ${{ needs.build.outputs.source_commit }}", checkout)
        self.assertIn("persist-credentials: false", checkout)
        self.assertIn("path: publish-source", checkout)
        # The downloaded four-file bundle contains no executable helper.
        # A fresh runner must materialize reviewed source, not rely on a prior job.
        self.assertLess(
            prefix.index("Verify signed provenance for every asset"),
            prefix.rindex("uses: actions/checkout@"),
        )


@unittest.skipUnless(
    sys.platform == "linux" and shutil.which("bash") and shutil.which("jq"),
    "publish job requires the Linux Bash/jq runner contract",
)
class PublishIdentityGateTests(unittest.TestCase):
    def test_real_publish_script_accepts_current_bundle_and_rejects_drift(self):
        script = publish_script("Verify asset set, checksums, and evidence identity")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = initialize_tagged_repository(root)
            assets = root / "release"
            release.create_source_archive(tag="v0.2.0", output_dir=assets, root=root)
            write_synthetic_wheel(assets)
            tag_object = run_git(root, "rev-parse", "refs/tags/v0.2.0")
            release.finalize_release(
                tag="v0.2.0",
                source_ref="refs/tags/v0.2.0",
                source_commit=commit,
                tag_object=tag_object,
                release_dir=assets,
                root=root,
            )
            original = json.loads((assets / "release-evidence.json").read_text())
            env = {
                **os.environ,
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_REF": "refs/tags/v0.2.0",
                "RELEASE_COMMIT": commit,
                "RELEASE_REF": "refs/tags/v0.2.0",
                "RELEASE_TAG_OBJECT": tag_object,
                "RELEASE_TAG": "v0.2.0",
                "RELEASE_VERSION": "0.2.0",
            }

            def execute(**changes):
                return subprocess.run(
                    ["bash", "-c", script],
                    cwd=root,
                    env={**env, **changes},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

            accepted = execute()
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertNotEqual(execute(RELEASE_COMMIT="f" * 40).returncode, 0)
            self.assertNotEqual(execute(GITHUB_RUN_ATTEMPT="2").returncode, 0)
            for field, value in (("schema_version", 1), ("contracts", {})):
                with self.subTest(field=field):
                    changed = {**original, field: value}
                    rewrite_evidence_and_checksums(assets, changed)
                    self.assertNotEqual(execute().returncode, 0)
            rewrite_evidence_and_checksums(assets, original)
            (assets / "extra.txt").write_text("synthetic unexpected asset")
            self.assertNotEqual(execute().returncode, 0)

    def test_attestation_failure_stops_before_later_assets(self):
        script = publish_script("Verify signed provenance for every asset")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "gh"
            log = root / "calls.txt"
            # This is an explicit test double, not a signature or live proof.
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nexit 1\n')
            fake.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=root,
                env={
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "CALL_LOG": str(log),
                    "GITHUB_REPOSITORY": "demidgost-sys/verified-video-pipeline",
                    "RELEASE_COMMIT": "a" * 40,
                    "RELEASE_REF": "refs/tags/v0.2.0",
                    "RELEASE_VERSION": "0.2.0",
                },
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            calls = log.read_text().splitlines()
            self.assertEqual(len(calls), 1)
            self.assertRegex(
                calls[0],
                re.escape(
                    "attestation verify release/verified-video-pipeline-0.2.0.tar.gz"
                ),
            )
