from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from verified_video_pipeline.errors import ContractError, IntegrityError
from verified_video_pipeline.hashing import require_identity, stable_sha256_file


class HashingTests(unittest.TestCase):
    def test_exact_byte_identity_detects_same_size_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.bin"
            path.write_bytes(b"AAAA")
            expected = stable_sha256_file(path)
            path.write_bytes(b"BBBB")
            with self.assertRaises(IntegrityError):
                require_identity(path, expected, label="artifact")

    def test_symlink_is_not_an_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.bin"
            target.write_bytes(b"data")
            link = root / "link.bin"
            link.symlink_to(target)
            with self.assertRaises(ContractError):
                stable_sha256_file(link)


if __name__ == "__main__":
    unittest.main()
