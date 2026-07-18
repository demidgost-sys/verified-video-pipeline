from __future__ import annotations

import json
import importlib.util
import re
import subprocess
import sys
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
