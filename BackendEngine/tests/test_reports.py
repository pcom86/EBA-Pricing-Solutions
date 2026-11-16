from __future__ import annotations

import importlib
import json
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from gap_config import load_settings
from gap_config.storage import Storage
from tests.fake_s3 import FakeS3


class ReportTests(unittest.TestCase):
	def setUp(self):
		self.temporary = tempfile.TemporaryDirectory()
		self.addCleanup(self.temporary.cleanup)
		self.root = Path(self.temporary.name)
		config = self.root / "config.json"
		config.write_text(json.dumps({"paths": {"staging": "staging", "cache": "cache"}}), encoding="utf-8")
		self.settings = load_settings(config, {})
		self.source = self.root / "example.ctr"
		self.source.write_bytes(b"synthetic CTR; parsed by stub")
		self.client = FakeS3()
		self.reporter = types.ModuleType("reporting")
		self.reporter.write_pricing_report = Mock(side_effect=self.write_report)
		self.engine = types.ModuleType("pricing_engine.engine")
		self.engine.price = Mock()
		self.engine.build_context = Mock(return_value=object())
		self.engine.price_all_products = Mock(return_value={"gla": object()})
		loader = types.ModuleType("ctr_loader.ctr_loader_v2")
		loader.load_ctr = Mock(return_value=types.SimpleNamespace(scheme=types.SimpleNamespace(scheme_name="Example", quote_number=1)))
		polars = types.ModuleType("polars")
		polars.read_parquet = Mock(return_value=types.SimpleNamespace(to_dicts=lambda: []))
		modules = patch.dict(sys.modules, {"polars": polars, "reporting": self.reporter,
			"pricing_engine.engine": self.engine, "ctr_loader.ctr_loader_v2": loader})
		modules.start()
		self.addCleanup(modules.stop)
		self.runner = importlib.import_module("pricing_engine.concurrent_runner")
		self.state = importlib.import_module("pricing_engine.async_state")
		for target, value in (("get_settings", self.settings),):
			setting_patch = patch.object(self.runner, target, return_value=value)
			setting_patch.start()
			self.addCleanup(setting_patch.stop)
		storage_patch = patch.object(self.runner, "Storage", side_effect=lambda root: Storage(root, self.client))
		storage_patch.start()
		self.addCleanup(storage_patch.stop)

	def write_report(self, workbook_path, write_xlsx=True, write_html=False, **kwargs):
		path = Path(workbook_path)
		if write_xlsx:
			path.write_bytes(b"xlsx")
		path.with_name(path.stem + "_calc.parquet").write_bytes(b"parquet")
		if write_html:
			path.with_suffix(".html").write_text("<html></html>", encoding="utf-8")

	def run_quote(self, **kwargs):
		return self.runner.run_single_quote(self.source, object(), object(),
			kwargs.pop("out_dir", self.root / "output"), wait_for_reports=True, **kwargs)

	def assert_drained(self):
		self.assertEqual(self.state.report_writes_pending(), 0)
		self.assertEqual(self.state.file_syncs_pending(), 0)
		self.assertEqual(self.state.db_writes_pending(), 0)
		self.assertEqual(self.state._dir_pending_consumers, {})
		self.assertEqual(list(self.settings.staging_dir.iterdir()), [])

	def test_local_outputs_exist_before_success_is_returned(self):
		with patch.object(self.runner, "_is_db_reachable", side_effect=AssertionError("DB should be disabled")):
			result = self.run_quote()
		self.assertEqual(result.status, "OK", result.error)
		self.assertEqual(Path(result.output_path).read_bytes(), b"xlsx")
		self.assertEqual(len(list((self.root / "output").glob("*_calc.parquet"))), 1)
		self.assert_drained()

	def test_s3_outputs_published_before_success_is_returned(self):
		result = self.run_quote(out_dir="s3://bucket/results")
		self.assertEqual(result.status, "OK", result.error)
		self.assertTrue(result.output_path.startswith("s3://bucket/results/"))
		self.assertEqual(len(self.client.uploads), 2)
		self.assertTrue(all(key.startswith("results/") and "\\" not in key for _, key in self.client.uploads))
		self.assert_drained()

	def test_html_and_parquet_modes(self):
		for mode, expected in (("html", {".html", ".parquet"}), (False, {".parquet"})):
			with self.subTest(mode=mode):
				self.client.uploads.clear()
				result = self.run_quote(out_dir="s3://bucket/results", write_report=mode)
				self.assertEqual(result.status, "OK", result.error)
				self.assertEqual({Path(key).suffix for _, key in self.client.uploads}, expected)
				self.assert_drained()

	def test_upload_failure_is_quote_error_and_cleans_staging(self):
		self.client.upload_error = OSError("upload interrupted")
		result = self.run_quote(out_dir="s3://bucket/results")
		self.assertEqual(result.status, "ERROR")
		self.assertIn("upload interrupted", result.error)
		self.assert_drained()

	def test_writer_failure_is_quote_error_and_cleans_staging(self):
		self.reporter.write_pricing_report.side_effect = OSError("disk full")
		result = self.run_quote()
		self.assertEqual(result.status, "ERROR")
		self.assertIn("disk full", result.error)
		self.assert_drained()

	def test_missing_requested_artifacts_is_an_error(self):
		self.reporter.write_pricing_report.side_effect = None
		result = self.run_quote()
		self.assertEqual(result.status, "ERROR")
		self.assertIn("did not produce", result.error)
		self.assert_drained()

	def test_pricing_failure_does_not_write_reports(self):
		self.engine.price_all_products.side_effect = ValueError("pricing failed")
		result = self.run_quote()
		self.assertEqual(result.status, "ERROR")
		self.reporter.write_pricing_report.assert_not_called()

	def test_explicitly_enabled_unreachable_db_is_not_success(self):
		self.runner.get_settings.return_value = replace(self.settings, db_enabled=True, db_connection_string="injected-secret")
		with patch.object(self.runner, "_is_db_reachable", return_value=False):
			result = self.run_quote()
		self.assertEqual(result.status, "ERROR")
		self.assertIn("unreachable", result.error)
		self.assertNotIn("injected-secret", result.error)
		self.assert_drained()

	def test_enabled_db_failure_and_success_are_awaited(self):
		self.runner.get_settings.return_value = replace(self.settings, db_enabled=True, db_connection_string="injected-secret")
		for status in ("failed", "success"):
			with self.subTest(status=status):
				database = types.ModuleType("pricing_engine.db_loader")
				database.load_run_to_db = Mock(return_value={"status": status, "error": "DB failure", "run_id": "test-run"})
				pandas = types.ModuleType("pandas")
				pandas.DataFrame = lambda rows: rows
				with patch.dict(sys.modules, {"pricing_engine.db_loader": database, "pandas": pandas}), \
					 patch.object(self.runner, "_is_db_reachable", return_value=True):
					result = self.run_quote(basis_folder="explicit-basis", assump_folder="explicit-assumptions")
				self.assertEqual(result.status, "OK" if status == "success" else "ERROR", result.error)
				self.assertEqual(database.load_run_to_db.call_args.kwargs["basis_snapshot_folder"], "explicit-basis")
				self.assertEqual(database.load_run_to_db.call_args.kwargs["assumption_snapshot_folder"], "explicit-assumptions")
				self.assert_drained()


if __name__ == "__main__":
	unittest.main()
