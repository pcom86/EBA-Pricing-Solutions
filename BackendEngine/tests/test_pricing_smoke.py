from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(os.environ.get("GAP_RUN_PRICING_SMOKE") == "1",
	"Real pricing smoke test requires the existing engine environment and GAP_RUN_PRICING_SMOKE=1")
class PricingSmokeTests(unittest.TestCase):
	def test_representative_quote_writes_readable_reports(self):
		dependencies = ("polars", "pandas", "openpyxl", "python_calamine", "rapidfuzz", "xlsxwriter")
		missing = [name for name in dependencies if importlib.util.find_spec(name) is None]
		self.assertFalse(missing, "Missing engine dependencies: " + ", ".join(missing))
		fixture = Path(os.environ.get("GAP_SMOKE_CTR", str(ROOT / "Test" / "Single run" /
			"Fictitious_Accel LSDB + FS CI No ORB + ParCov+PRO+EduG 30.07.2026.ctr")))
		self.assertTrue(fixture.is_file(), f"Representative CTR not found: {fixture}")
		with tempfile.TemporaryDirectory(prefix="gap-pricing-smoke-") as temporary:
			work = Path(temporary)
			environment = dict(os.environ, GAP_DB_ENABLED="false", PYTHONDONTWRITEBYTECODE="1",
				GAP_CTR_CACHE_DIR=str(work / "cache"), GAP_STAGING_DIR=str(work / "staging"))
			command = [sys.executable, "-B", "-m", "pricing_engine", "--ctr-file", str(fixture),
				"--output-dir", str(work / "output"), "--workers", "1", "--report", "xlsx"]
			for variable, option in (("GAP_SMOKE_BASIS_DIR", "--basis-dir"),
								 ("GAP_SMOKE_ASSUMPTION_DIR", "--assumption-dir")):
				if environment.get(variable):
					command.extend([option, environment[variable]])
			result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
				text=True, encoding="utf-8", errors="replace", timeout=600)
			self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-12000:])
			workbooks = list((work / "output").glob("*.xlsx"))
			parquets = list((work / "output").glob("*_calc.parquet"))
			self.assertEqual(len(workbooks), 1)
			self.assertEqual(len(parquets), 1)
			import openpyxl
			import polars as pl
			workbook = openpyxl.load_workbook(workbooks[0], read_only=True, data_only=True)
			try:
				self.assertIn("Meta", workbook.sheetnames)
				self.assertIn("Pricing Outputs", workbook.sheetnames)
			finally:
				workbook.close()
			self.assertGreater(pl.read_parquet(parquets[0]).height, 0)


if __name__ == "__main__":
	unittest.main()
