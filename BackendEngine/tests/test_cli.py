from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gap_config import get_settings
from pricing_engine.cli import main

from tests.fake_s3 import FakeS3


class Summary:
	def __init__(self, failed=False):
		self.height = 1
		self.failed = failed

	def __getitem__(self, name):
		return "OK"

	def filter(self, expression):
		return types.SimpleNamespace(height=int(self.failed),
			iter_rows=lambda named: [{"ctr_name": "example.ctr", "error": "publication failed"}])


class CliTests(unittest.TestCase):
	def setUp(self):
		self.temporary = tempfile.TemporaryDirectory()
		self.addCleanup(self.temporary.cleanup)
		self.root = Path(self.temporary.name)
		self.config = self.root / "config.json"
		self.data = {"paths": {"input": "inputs", "output": "output", "cache": "cache", "staging": "staging",
			"basis": "basis", "assumptions": "assumptions"}, "snapshots": {"basis": "v1", "assumptions": "v1"}}
		self.write_config()
		for name in ("inputs", "basis/v1", "assumptions/v1"):
			(self.root / name).mkdir(parents=True)
		(self.root / "inputs" / "example.CTR").write_bytes(b"CTR fixture")
		for name in ("basis", "assumptions"):
			(self.root / name / "v1" / "table.parquet").write_bytes(b"snapshot fixture")
		self.runner = types.ModuleType("pricing_engine.concurrent_runner")
		self.runner.load_shared_snapshots = Mock(return_value=(object(), object()))
		self.runner.run_quotes_concurrent = Mock(return_value=Summary())
		modules = patch.dict(sys.modules, {"pricing_engine.concurrent_runner": self.runner})
		modules.start()
		self.addCleanup(modules.stop)
		environment = patch.dict(os.environ, {"GAP_CONFIG_FILE": str(self.config)}, clear=True)
		environment.start()
		self.addCleanup(environment.stop)
		self.addCleanup(get_settings.cache_clear)

	def write_config(self):
		self.config.write_text(json.dumps(self.data), encoding="utf-8")

	def test_config_check_does_not_price_or_import_core_dependencies(self):
		with patch.dict(sys.modules, {"polars": None, "boto3": None}):
			self.assertEqual(main(["--check-config"]), 0)
		self.runner.run_quotes_concurrent.assert_not_called()

	def test_cli_overrides_json_and_waits_for_reports(self):
		output = str(self.root / "override")
		self.assertEqual(main(["--output-dir", output, "--workers", "2", "--report", "html"]), 0)
		call = self.runner.run_quotes_concurrent.call_args
		self.assertEqual(call.args[3], output)
		self.assertTrue(call.kwargs["wait_for_reports"])
		self.assertEqual(call.kwargs["max_workers"], 2)
		self.assertEqual(call.kwargs["write_report"], "html")
		self.assertEqual(call.args[0][0].name, "example.CTR")
		self.assertEqual(call.kwargs["basis_folder"], str(self.root / "basis" / "v1"))

	def test_report_failure_returns_nonzero(self):
		self.runner.run_quotes_concurrent.return_value = Summary(failed=True)
		with self.assertLogs("pricing_engine.cli", level="ERROR"):
			self.assertEqual(main([]), 1)

	def test_no_inputs_and_invalid_worker_count_fail(self):
		(self.root / "inputs" / "example.CTR").unlink()
		with self.assertLogs("pricing_engine.cli", level="ERROR"):
			self.assertEqual(main([]), 1)
		with self.assertLogs("pricing_engine.cli", level="ERROR"):
			self.assertEqual(main(["--workers", "0", "--check-config"]), 1)
		self.runner.run_quotes_concurrent.assert_not_called()

	def test_missing_database_secret_fails_before_pricing(self):
		with patch.dict(os.environ, {"GAP_DB_ENABLED": "true"}), self.assertLogs("pricing_engine.cli", level="ERROR"):
			self.assertEqual(main([]), 1)
		self.runner.run_quotes_concurrent.assert_not_called()

	def test_s3_inputs_and_snapshots_stage_before_pricing(self):
		client = FakeS3()
		for key in ("inputs/example.ctr", "basis/v1/table.parquet", "assumptions/v1/table.parquet"):
			client.seed("bucket", key)
		self.data["paths"].update({"input": "s3://bucket/inputs", "basis": "s3://bucket/basis",
			"assumptions": "s3://bucket/assumptions", "output": "s3://bucket/output"})
		self.write_config()
		factory = Mock(return_value=client)
		with patch.dict(sys.modules, {"boto3": types.SimpleNamespace(client=factory)}):
			self.assertEqual(main([]), 0)
		call = self.runner.run_quotes_concurrent.call_args
		self.assertEqual(call.args[3], "s3://bucket/output")
		self.assertTrue(call.args[0][0].is_file())
		self.assertTrue(Path(call.kwargs["basis_folder"]).is_dir())
		self.assertTrue(Path(call.kwargs["assump_folder"]).is_dir())
		self.assertTrue(all(args == ("s3",) and not kwargs for args, kwargs in factory.call_args_list))


if __name__ == "__main__":
	unittest.main()
