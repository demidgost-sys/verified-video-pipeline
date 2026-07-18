from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from verified_video_pipeline.errors import ContractError, LeaseBusy
from verified_video_pipeline.locks import FileLease, heavy_media_lease


class LockTests(unittest.TestCase):
    def test_second_lease_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lease.lock"
            with FileLease(path, label="test"):
                with self.assertRaises(LeaseBusy):
                    with FileLease(path, label="test"):
                        self.fail("second lease unexpectedly acquired")

    def test_lease_refuses_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("must stay intact", encoding="utf-8")
            link = root / "lease.lock"
            link.symlink_to(target)
            with self.assertRaises(ContractError):
                with FileLease(link, label="test"):
                    self.fail("symlink lease unexpectedly acquired")
            self.assertEqual(target.read_text(encoding="utf-8"), "must stay intact")

    def test_lease_refuses_hardlink_without_modifying_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside.txt"
            target.write_text("must stay intact", encoding="utf-8")
            lease = root / "lease.lock"
            lease.hardlink_to(target)
            with self.assertRaises(ContractError):
                with FileLease(lease, label="test"):
                    self.fail("hardlink lease unexpectedly acquired")
            self.assertEqual(target.read_text(encoding="utf-8"), "must stay intact")

    def test_lease_refuses_unmarked_regular_file_without_modifying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lease.lock"
            original = b"ordinary file, not a VVP lease\x00\xff"
            path.write_bytes(original)
            path.chmod(0o640)
            original_mode = stat.S_IMODE(path.stat().st_mode)

            with self.assertRaises(ContractError):
                with FileLease(path, label="test"):
                    self.fail("unmarked regular file unexpectedly became a lease")

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode)

    def test_heavy_override_cannot_alias_an_unmarked_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-source.mp4"
            original = b"DO-NOT-MODIFY"
            path.write_bytes(original)
            path.chmod(0o640)
            original_mode = stat.S_IMODE(path.stat().st_mode)

            with mock.patch.dict(os.environ, {"VVP_HEAVY_LEASE": str(path)}):
                with self.assertRaises(ContractError):
                    with heavy_media_lease():
                        self.fail("unmarked override unexpectedly became a lease")

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode)

    def test_stale_valid_marker_can_be_reused_for_same_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lease.lock"
            with FileLease(path, label="test"):
                pass

            stale_marker = path.read_bytes()
            self.assertTrue(stale_marker.startswith(b"VVP_FILE_LEASE_V1\n"))
            with FileLease(path, label="test"):
                self.assertTrue(path.read_bytes().startswith(b"VVP_FILE_LEASE_V1\n"))

    def test_valid_marker_for_other_label_is_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lease.lock"
            with FileLease(path, label="first"):
                pass
            original = path.read_bytes()
            path.chmod(0o640)
            original_mode = stat.S_IMODE(path.stat().st_mode)

            with self.assertRaises(ContractError):
                with FileLease(path, label="second"):
                    self.fail("marker for another label unexpectedly accepted")

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode)


if __name__ == "__main__":
    unittest.main()
