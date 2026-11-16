from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctr_loader import ctr_cache


class CtrCacheTests(unittest.TestCase):
	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.root = Path(temporary.name)
		self.mount = self.root / "network mount"
		self.mount.mkdir()
		self.source = self.mount / "quote.ctr"
		self.source.write_bytes(b"quote data")
		self.cache = self.root / "cache"

	def test_detects_network_mount_filesystem_from_mountinfo(self):
		mountinfo = self.root / "mountinfo"
		mountinfo.write_text(
			f"36 25 0:32 / {_escape_mount(self.mount)} rw,relatime - nfs4 server:/share rw\n",
			encoding="utf-8",
		)
		self.assertTrue(ctr_cache._is_linux_network_mount(self.source, mountinfo))

	def test_nearest_mount_type_wins_for_nested_local_mount(self):
		nested = self.mount / "local"
		nested.mkdir()
		path = nested / "quote.ctr"
		path.write_bytes(b"quote")
		mountinfo = self.root / "mountinfo"
		mountinfo.write_text(
			f"36 25 0:32 / {_escape_mount(self.mount)} rw - nfs server:/share rw\n"
			f"37 36 0:33 / {_escape_mount(nested)} rw - ext4 /dev/test rw\n",
			encoding="utf-8",
		)
		self.assertFalse(ctr_cache._is_linux_network_mount(path, mountinfo))

	def test_network_ctr_is_staged_and_local_ctr_is_returned_unchanged(self):
		settings = SimpleNamespace(cache_dir=self.cache)
		with patch.object(ctr_cache, "local_file", side_effect=lambda path: Path(path)), \
			 patch.object(ctr_cache, "get_settings", return_value=settings), \
			  patch.object(ctr_cache, "_should_cache_ctr", return_value=True):
			cached = ctr_cache.local_ctr_path(self.source)
		self.assertNotEqual(cached, self.source)
		self.assertEqual(cached.read_bytes(), self.source.read_bytes())

		with patch.object(ctr_cache, "local_file", side_effect=lambda path: Path(path)), \
			 patch.object(ctr_cache, "get_settings", return_value=settings), \
			  patch.object(ctr_cache, "_should_cache_ctr", return_value=False):
			self.assertEqual(ctr_cache.local_ctr_path(self.source), self.source)

	def test_platform_cache_policy_preserves_unc_and_limits_linux_copy_to_network_mount(self):
		with patch.object(ctr_cache, "_is_linux_network_mount", return_value=True):
			self.assertTrue(ctr_cache._should_cache_ctr(self.source, platform="posix"))
		with patch.object(ctr_cache, "_is_linux_network_mount", return_value=False):
			self.assertFalse(ctr_cache._should_cache_ctr(self.source, platform="posix"))
		self.assertFalse(ctr_cache._should_cache_ctr(self.source, platform="nt"))
		self.assertTrue(ctr_cache._should_cache_ctr(Path(r"\\server\share\quote.ctr"), platform="nt"))


def _escape_mount(path: Path) -> str:
	return str(path).replace("\\", "\\134").replace(" ", "\\040")


if __name__ == "__main__":
	unittest.main()
