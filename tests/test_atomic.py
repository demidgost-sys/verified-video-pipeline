from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from verified_video_pipeline.atomic import (
    atomic_write_json,
    canonical_json_bytes,
    json_sha256,
    read_json,
)
from verified_video_pipeline.errors import ContractError


class AtomicTests(unittest.TestCase):
    def test_canonical_json_is_order_independent(self) -> None:
        left = {"b": 2, "a": [3, 1]}
        right = {"a": [3, 1], "b": 2}
        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(json_sha256(left), json_sha256(right))

    def test_atomic_write_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_write_json(path, {"revision": 1})
            self.assertEqual(json.loads(path.read_text()), {"revision": 1})
            self.assertFalse(
                any(
                    item.name.startswith(".state.json.")
                    for item in path.parent.iterdir()
                )
            )

    def test_atomic_write_refuses_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real.json"
            real.write_text("{}\n", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaises(ContractError):
                atomic_write_json(link, {"unsafe": True})
            self.assertEqual(real.read_text(encoding="utf-8"), "{}\n")

    def test_read_json_rejects_non_finite_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text('{"value":NaN}\n', encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "non-finite"):
                read_json(path)


if __name__ == "__main__":
    unittest.main()
