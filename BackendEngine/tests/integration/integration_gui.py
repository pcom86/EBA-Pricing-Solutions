"""Explicit real-dependency test; deliberately excluded from test_*.py discovery."""
from __future__ import annotations

import importlib
import math
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class GuiIntegrationTests(unittest.TestCase):
    def test_mounted_gui_pricing_views_and_reports(self):
        import openpyxl
        import polars as pl
        from gap_config import get_settings
        from gap_pricing_gui.file_access import batch_ctr_files, validate_gui_settings
        from PySide6 import QtWidgets

        self.assertEqual(os.environ.get("QT_QPA_PLATFORM"), "offscreen")
        self.assertIn("GAP_GUI_TEST_CTR", os.environ, "Supply an approved mounted CTR via GAP_GUI_TEST_CTR")
        fixture = Path(os.environ["GAP_GUI_TEST_CTR"]).resolve(strict=True)
        settings = validate_gui_settings()
        self.assertFalse(settings.db_enabled, "This integration target requires GAP_DB_ENABLED=false")
        paths = batch_ctr_files(settings.input_dir)
        self.assertGreaterEqual(len(paths), 2, "Mount at least two approved CTRs to exercise batch pricing")
        self.assertIn(fixture, paths, "The single-quote fixture must be in GAP_INPUT_DIR")
        for name in ("gap_pricing_gui", "gap_config", "pricing_engine", "ctr_loader", "reporting"):
            module = importlib.import_module(name)
            for origin in module.__path__:
                self.assertTrue(Path(origin).resolve().is_relative_to(ROOT), (name, origin))

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        errors = []
        with tempfile.TemporaryDirectory(prefix="gui-test-", dir=settings.output_dir) as output:  # noqa: SIM117
            with patch.dict(os.environ, {"GAP_OUTPUT_DIR": output}):
                get_settings.cache_clear()
                with patch.object(QtWidgets.QMessageBox, "critical", side_effect=lambda *args: errors.append(args[1:])), \
                    patch.object(QtWidgets.QMessageBox, "warning", side_effect=lambda *args: errors.append(args[1:])), \
                    patch.object(QtWidgets.QMessageBox, "information", return_value=QtWidgets.QMessageBox.Ok):
                    from gap_pricing_gui import quote_runner
                    from gap_pricing_gui.launch_gap_gui import main
                    self.assertEqual(main(), 0)
                    window = app._gap_main_window

                    def wait_until(condition, timeout=600):
                        deadline = time.monotonic() + timeout
                        while not condition() and time.monotonic() < deadline:
                            app.processEvents()
                            self.assertFalse(errors, errors)
                            time.sleep(0.02)
                        app.processEvents()
                        self.assertFalse(errors, errors)
                        self.assertTrue(condition(), "Timed out waiting for GUI work")

                    try:
                        wait_until(lambda: window._startup_data_ready and window.priceb.isEnabled())
                        self.assertEqual(window.workers.value(), settings.pricing_workers)
                        self.assertFalse(window.load_db_btn.isEnabled())
                        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(fixture), "")) as dialog:
                            window.on_browse()
                            self.assertEqual(dialog.call_args.args[2], settings.input_dir)
                        self.assertEqual(window.path.text(), str(fixture))
                        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=("", "")):
                            window.on_browse()
                        self.assertEqual(window.path.text(), str(fixture))

                        window.on_price()
                        self.assertFalse(errors, errors)
                        self.assertIsNotNone(window.result)
                        self.assertTrue(window.result.per_product_results)
                        premium = window.result.aggregates["total_annual_premium"]
                        self.assertTrue(math.isfinite(premium))
                        self.assertGreater(premium, 0)
                        wait_until(lambda: window._cb_grid is not None and window._po_grid is not None)
                        window.nav.setCurrentRow(2)
                        window.nav.setCurrentRow(3)
                        self.assertGreater(window.po_table.rowCount(), 0)
                        window.on_save()
                        window.on_save_html()
                        self.assertFalse(errors, errors)
                        self.assertTrue(Path(window.result.xlsx_path).is_file())
                        self.assertTrue(Path(window.result.parquet_path).is_file())
                        self.assertIn("<html", Path(window.result.html_path).read_text(encoding="utf-8").lower())

                        with patch.object(quote_runner.Storage, "put_file", side_effect=PermissionError("publication failed")):  # noqa: SIM117
                            with self.assertRaisesRegex(PermissionError, "publication failed"):
                                quote_runner.save_report(window.result, write_xlsx=False, write_parquet=False, write_html=True)
                        with patch("reporting.write_pricing_report"):  # noqa: SIM117
                            with self.assertRaisesRegex(RuntimeError, "did not produce"):
                                quote_runner.save_report(window.result, write_xlsx=False, write_parquet=False, write_html=True)
                        staging_before = set(settings.staging_dir.iterdir())
                        def fail_after_partial_write(*args, **kwargs):
                            kwargs["workbook_path"].write_bytes(b"partial report")
                            raise RuntimeError("report generation failed")
                        with patch("reporting.write_pricing_report", side_effect=fail_after_partial_write):  # noqa: SIM117
                            with self.assertRaisesRegex(RuntimeError, "report generation failed"):
                                quote_runner.save_report(window.result)
                        self.assertEqual(set(settings.staging_dir.iterdir()), staging_before)

                        window.bfolder.setText("s3://bucket/unsupported")
                        window.on_batch()
                        self.assertIsNone(window._batch_thread)
                        self.assertTrue(errors)
                        self.assertIn("mounted filesystem", str(errors))
                        errors.clear()
                        window.bfolder.setText(settings.input_dir)
                        window.boutput.setText(str(Path(output) / "batch"))
                        window.on_batch()
                        wait_until(lambda: window._batch_thread is not None and not window._batch_thread.isRunning() and window.runb.isEnabled())
                        self.assertEqual(len(window.batch_model._rows), len(paths))
                        status_index = window.batch_model._cols.index("status")
                        self.assertTrue(all(row[status_index] == "OK" for row in window.batch_model._rows), window.batch_model._rows)
                        self.assertEqual(len(window._batch_cache), len(paths))
                        from gap_pricing_gui.batch_detail_dialog import (
                            BatchDetailDialog,
                        )
                        source, (ctr, assump, results) = next(iter(window._batch_cache.items()))
                        detail = BatchDetailDialog(ctr, assump, results, source, out_dir=window.boutput.text(), parent=window)
                        detail._show_tab(1)
                        detail._show_tab(2)
                        self.assertIsNotNone(detail._cb_grid)
                        self.assertIsNotNone(detail._po_grid)
                        detail.on_save_html()
                        detail.close()
                        self.assertFalse(errors, errors)

                        workbooks = list(Path(output).rglob("*.xlsx"))
                        parquets = list(Path(output).rglob("*_calc.parquet"))
                        self.assertEqual(len(workbooks), len(paths) + 1)
                        self.assertEqual(len(parquets), len(paths) + 1)
                        for path in workbooks:
                            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
                            try:
                                for sheet in ("Meta", "Pricing Outputs", "Category Breakdown"):
                                    self.assertIn(sheet, workbook.sheetnames)
                            finally:
                                workbook.close()
                        for path in parquets:
                            self.assertGreater(pl.read_parquet(path).height, 0)
                        for path in Path(output).rglob("*.html"):
                            self.assertIn("<html", path.read_text(encoding="utf-8").lower())
                    finally:
                        for name in ("_startup", "_view_prewarm", "_batch_thread"):
                            thread = getattr(window, name, None)
                            if thread is not None:
                                thread.wait(600000)
                        window.close()
                        app.processEvents()
                get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
