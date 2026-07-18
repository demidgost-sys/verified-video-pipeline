from __future__ import annotations

import os
import json
import importlib.util
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from urllib.parse import unquote

from jsonschema import Draft202012Validator

from verified_video_pipeline import __version__


ROOT = Path(__file__).resolve().parents[1]


def load_public_audit_module():
    path = ROOT / "scripts" / "audit_public_tree.py"
    spec = importlib.util.spec_from_file_location("vvp_public_audit", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load public audit module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepositoryContractTests(unittest.TestCase):
    def initialize_fixture_repository(
        self,
        repository: Path,
        *,
        commit_message: str = "fixture commit",
        commit_email: str | None = None,
    ) -> dict[str, str]:
        noreply = "123+fixture" + "@" + "users.noreply.github.com"
        commit_email = commit_email or noreply
        environment = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Public Fixture",
            "GIT_AUTHOR_EMAIL": commit_email,
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
            "GIT_COMMITTER_NAME": "Public Fixture",
            "GIT_COMMITTER_EMAIL": commit_email,
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
        }
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=repository,
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "--message", commit_message],
            cwd=repository,
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return environment

    def audit_fixture_history(
        self,
        *,
        commit_message: str = "fixture commit",
        commit_email: str | None = None,
        tag_message: str | None = None,
        tag_email: str | None = None,
    ) -> list[str]:
        noreply = "123+fixture" + "@" + "users.noreply.github.com"
        tag_email = tag_email or noreply
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            environment = self.initialize_fixture_repository(
                repository,
                commit_message=commit_message,
                commit_email=commit_email,
            )
            if tag_message is not None:
                tag_environment = {
                    **environment,
                    "GIT_COMMITTER_EMAIL": tag_email,
                    "GIT_COMMITTER_DATE": "2000-01-02T00:00:00+0000",
                }
                subprocess.run(
                    [
                        "git",
                        "tag",
                        "--annotate",
                        "fixture-v1",
                        "--message",
                        tag_message,
                    ],
                    cwd=repository,
                    env=tag_environment,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            audit = load_public_audit_module()
            original_root = audit.ROOT
            try:
                audit.ROOT = repository
                return audit.audit_history()
            finally:
                audit.ROOT = original_root

    def test_public_tree_audit_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/audit_public_tree.py"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_non_github_email_is_denied_in_source_text(self) -> None:
        audit = load_public_audit_module()
        private_email = "private" + "@" + "example.org"
        failures = audit.audit_blob(
            "README.md", private_email.encode(), origin="worktree"
        )
        self.assertTrue(any("private email address" in item for item in failures))
        path_failures = audit.audit_blob(
            f"safe/{private_email}/config.txt", b"public fixture\n", origin="worktree"
        )
        self.assertTrue(
            any("private email address" in item for item in path_failures),
            path_failures,
        )

    def test_audit_policy_source_does_not_hide_its_literal_block(self) -> None:
        audit = load_public_audit_module()
        private_email = "private" + "@" + "example.org"
        data = (
            f"# audit-literals:start\n{private_email}\n# audit-literals:end\n"
        ).encode()
        failures = audit.audit_blob(
            "scripts/audit_public_tree.py", data, origin="worktree"
        )
        self.assertTrue(any("private email address" in item for item in failures))

    def test_reachable_commit_message_uses_blob_privacy_patterns(self) -> None:
        channel_id = "UC" + "A" * 22
        private_email = "private" + "@" + "example.org"
        failures = self.audit_fixture_history(
            commit_message=f"do not publish channel {channel_id}; contact {private_email}"
        )
        self.assertTrue(
            any(
                "commit message" in item and "private-data pattern" in item
                for item in failures
            ),
            failures,
        )
        self.assertTrue(
            any(
                "commit message" in item and "private email address" in item
                for item in failures
            ),
            failures,
        )

    def test_annotated_tag_message_uses_blob_privacy_patterns(self) -> None:
        playlist_id = "PL" + "A" * 11
        failures = self.audit_fixture_history(
            tag_message=f"do not publish playlist {playlist_id}"
        )
        self.assertTrue(
            any(
                "annotated tag message" in item and "private-data pattern" in item
                for item in failures
            ),
            failures,
        )

    def test_other_git_metadata_headers_use_privacy_patterns(self) -> None:
        audit = load_public_audit_module()
        noreply = "123+fixture" + "@" + "users.noreply.github.com"
        private_email = "private" + "@" + "example.org"
        commit = (
            f"tree {'a' * 40}\n"
            f"author Public Fixture <{noreply}> 946684800 +0000\n"
            f"committer Public Fixture <{noreply}> 946684800 +0000\n"
            f"encoding {private_email}\n"
            "\n"
            "public fixture commit\n"
        ).encode()
        failures = audit._audit_metadata_object("b" * 40, "commit", commit)
        self.assertTrue(
            any(
                "Git metadata headers" in item and "private email address" in item
                for item in failures
            ),
            failures,
        )

    def test_non_github_git_identity_emails_are_denied(self) -> None:
        private_email = "private" + "@" + "example.org"
        commit_failures = self.audit_fixture_history(commit_email=private_email)
        self.assertTrue(
            any("private Git author email" in item for item in commit_failures),
            commit_failures,
        )

        tag_failures = self.audit_fixture_history(
            tag_message="fixture tag", tag_email=private_email
        )
        self.assertTrue(
            any("private Git tagger email" in item for item in tag_failures),
            tag_failures,
        )

    def test_github_noreply_commit_and_tag_metadata_are_allowed(self) -> None:
        self.assertEqual(
            self.audit_fixture_history(tag_message="public fixture tag"), []
        )

    def test_historical_denied_name_survives_content_preserving_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            environment = self.initialize_fixture_repository(repository)
            (repository / "credentials.json").write_text("{}\n", encoding="utf-8")
            private_email = "private" + "@" + "example.org"
            private_directory = repository / private_email
            private_directory.mkdir()
            (private_directory / "config.txt").write_text(
                "public fixture\n", encoding="utf-8"
            )
            for arguments in (
                ("add", "credentials.json", f"{private_email}/config.txt"),
                ("commit", "--message", "add synthetic credentials fixture"),
                ("mv", "credentials.json", "safe.txt"),
                ("commit", "--message", "rename synthetic fixture"),
            ):
                subprocess.run(
                    ["git", *arguments],
                    cwd=repository,
                    env=environment,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            audit = load_public_audit_module()
            original_root = audit.ROOT
            try:
                audit.ROOT = repository
                failures = audit.audit_history()
            finally:
                audit.ROOT = original_root
        self.assertTrue(
            any(
                "credential-shaped filename: credentials.json" in item
                for item in failures
            ),
            failures,
        )
        self.assertTrue(
            any("private email address" in item for item in failures), failures
        )

    def test_shallow_history_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.initialize_fixture_repository(repository)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            (repository / ".git" / "shallow").write_text(
                f"{commit}\n", encoding="ascii"
            )
            audit = load_public_audit_module()
            original_root = audit.ROOT
            try:
                audit.ROOT = repository
                failures = audit.audit_history()
            finally:
                audit.ROOT = original_root
        self.assertIn(
            "history: full Git history unavailable (shallow repository)", failures
        )

    def test_historical_mode_change_cannot_hide_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            environment = self.initialize_fixture_repository(repository)
            path = repository / "alias.txt"
            path.write_bytes(b"target2")
            for arguments in (
                ("add", "alias.txt"),
                ("commit", "--message", "add regular synthetic fixture"),
            ):
                subprocess.run(
                    ["git", *arguments],
                    cwd=repository,
                    env=environment,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            path.unlink()
            path.symlink_to("target2")
            for arguments in (
                ("add", "alias.txt"),
                ("commit", "--message", "change synthetic fixture mode"),
            ):
                subprocess.run(
                    ["git", *arguments],
                    cwd=repository,
                    env=environment,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            audit = load_public_audit_module()
            original_root = audit.ROOT
            try:
                audit.ROOT = repository
                failures = audit.audit_history()
            finally:
                audit.ROOT = original_root
        self.assertTrue(
            any("non-regular tree entry: alias.txt" in item for item in failures),
            failures,
        )

    def test_runtime_and_credential_filenames_are_denied(self) -> None:
        audit = load_public_audit_module()
        for name in (
            "project.json",
            "approved-plan.json",
            "build-receipt.json",
            "release-manifest.json",
            ".master.staged.mp4",
            "credentials.json",
            "client_secret_demo.json",
            "id_ed25519",
            "signing.key",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(audit.denied_filename(name))

    def test_runtime_filenames_are_ignored_by_default(self) -> None:
        ignored = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        audit = load_public_audit_module()
        self.assertTrue(audit.RUNTIME_BASENAMES <= ignored)

    def test_version_has_one_source_of_truth_at_release(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["version"], __version__)

    def test_json_contracts_parse(self) -> None:
        for path in sorted((ROOT / "schemas").glob("*.json")) + sorted(
            (ROOT / "examples").glob("*.json")
        ):
            with self.subTest(path=path.name):
                self.assertIsInstance(
                    json.loads(path.read_text(encoding="utf-8")), dict
                )

    def test_json_schemas_pass_their_metaschema(self) -> None:
        for path in sorted((ROOT / "schemas").glob("*.schema.json")):
            with self.subTest(path=path.name):
                Draft202012Validator.check_schema(
                    json.loads(path.read_text(encoding="utf-8"))
                )

    def test_example_plan_matches_structural_schema(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "edit-plan.schema.json").read_text(encoding="utf-8")
        )
        example = json.loads(
            (ROOT / "examples" / "edit-plan.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(example)

    def test_release_schema_rejects_empty_evidence_objects(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "release-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        invalid = {
            "schema_version": 1,
            "release_id": "sha256-0123456789abcdef",
            "source": {},
            "approved_plan": {},
            "artifact": {},
            "qa": {},
            "provenance": {},
        }
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))

    def test_workflow_actions_are_pinned_to_commit_sha(self) -> None:
        uses = re.compile(r"^\s*uses:\s*[^\s@]+@([^\s#]+)", re.MULTILINE)
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            revisions = uses.findall(path.read_text(encoding="utf-8"))
            self.assertTrue(revisions, f"no actions found in {path.name}")
            for revision in revisions:
                with self.subTest(path=path.name, revision=revision):
                    self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_public_audit_workflow_fetches_full_history(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        audit_position = workflow.index("run: python scripts/audit_public_tree.py")
        checkout_position = workflow.rindex(
            "uses: actions/checkout@", 0, audit_position
        )
        checkout_step_end = workflow.index("\n      - name:", checkout_position)
        checkout_step = workflow[checkout_position:checkout_step_end]
        self.assertRegex(checkout_step, r"(?m)^\s+fetch-depth:\s*0\s*$")
        self.assertRegex(checkout_step, r"(?m)^\s+fetch-tags:\s*true\s*$")
        self.assertRegex(checkout_step, r"(?m)^\s+persist-credentials:\s*false\s*$")

    def test_relative_markdown_links_resolve(self) -> None:
        link = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
        for document in sorted(ROOT.rglob("*.md")):
            if ".git" in document.parts:
                continue
            for raw_target in link.findall(document.read_text(encoding="utf-8")):
                target = raw_target.strip("<>").split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = document.parent / unquote(target)
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(
                        resolved.exists(), f"broken link: {document}: {target}"
                    )


if __name__ == "__main__":
    unittest.main()
