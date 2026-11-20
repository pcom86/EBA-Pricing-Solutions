from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gap_config import ConfigurationError, load_settings
from gap_pricing_gui import file_access


class GuiFileAccessTests(unittest.TestCase):
	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.root = Path(temporary.name)
		config = self.root / "config.json"
		config.write_text(json.dumps({"paths": {
			"input": "input", "output": "output", "basis": "basis", "assumptions": "assumptions",
			"cache": "cache", "staging": "staging",
		}}), encoding="utf-8")
		self.settings = load_settings(config, {})
		for name in ("input", "basis", "assumptions"):
			(self.root / name).mkdir()
		replacement = patch.object(file_access, "get_settings", return_value=self.settings)
		replacement.start()
		self.addCleanup(replacement.stop)

	def test_preflight_reuses_settings_and_creates_only_writable_locations(self):
		self.assertIs(file_access.validate_gui_settings(), self.settings)
		self.assertTrue((self.root / "output").is_dir())
		self.assertTrue(self.settings.cache_dir.is_dir())
		self.assertTrue(self.settings.staging_dir.is_dir())
		self.assertEqual(list((self.root / "input").iterdir()), [])

	def test_relative_paths_use_configuration_directory(self):
		self.assertEqual(file_access.input_directory("input"), self.root / "input")
		self.assertEqual(file_access.output_directory("reports/nested"), self.root / "reports/nested")

	def test_s3_is_rejected_at_each_gui_boundary(self):
		for operation in (file_access.input_file, file_access.input_directory,
						  file_access.batch_ctr_files, file_access.output_directory):
			with self.subTest(operation=operation.__name__), \
					self.assertRaisesRegex(ConfigurationError, "mounted filesystem"):
				operation("s3://bucket/ctrs")

	def test_blank_and_unsupported_locations_are_rejected(self):
		for location in ("", "   ", "https://example.invalid/input"):
			with self.subTest(location=location), self.assertRaises(ConfigurationError):
				file_access.input_directory(location)

	def test_missing_and_directory_inputs_fail(self):
		for path in (self.root / "missing.ctr", self.root / "input"):
			with self.subTest(path=path), self.assertRaises(ConfigurationError):
				file_access.input_file(path)
		with self.assertRaises(ConfigurationError):
			file_access.input_directory(self.root / "missing")

	def test_batch_matches_uppercase_and_lowercase_without_recursion(self):
		folder = self.root / "input"
		for name in ("b.CTR", "a.ctr", "ignored.xlsx"):
			(folder / name).write_bytes(b"fixture")
		(folder / "nested.ctr").mkdir()
		(folder / "nested.ctr" / "hidden.ctr").write_bytes(b"fixture")
		self.assertEqual([p.name for p in file_access.batch_ctr_files(folder)], ["a.ctr", "b.CTR"])

	def test_empty_batch_fails(self):
		with self.assertRaisesRegex(ConfigurationError, "No .ctr files"):
			file_access.batch_ctr_files(self.root / "input")

	def test_output_file_and_permission_failures_are_actionable(self):
		target = self.root / "not-a-directory"
		target.write_bytes(b"existing")
		with self.assertRaisesRegex(ConfigurationError, "writable directory"):
			file_access.output_directory(target)
		with patch.object(file_access.tempfile, "TemporaryFile", side_effect=PermissionError("read only")), \
				self.assertRaisesRegex(ConfigurationError, "writable directory"):
			file_access.output_directory()


if __name__ == "__main__":
	unittest.main()
