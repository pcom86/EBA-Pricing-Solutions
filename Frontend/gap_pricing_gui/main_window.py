from __future__ import annotations

import logging
from pathlib import Path

from gap_config import ConfigurationError
from PySide6 import QtCore, QtWidgets

from .batch_detail_dialog import BatchDetailDialog
from .category_split_bar import CategorySplitBar
from .file_access import (
    batch_ctr_files,
    input_directory,
    input_file,
    output_directory,
    validate_gui_settings,
)
from .grid_view import ProductPillGridView, populate_table_from_grid
from .models import FastModel
from .single_quote_view import SingleQuoteView
from .theme import EXPRESS_COMBO_QSS, MARGIN_EDIT_QSS, NAV_QSS
from .workers import BatchThread, StartupThread, ViewPrewarmThread

logger = logging.getLogger(__name__)

class GapWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        try:
            self.settings = validate_gui_settings()
        except (ConfigurationError, OSError) as exc:
            logger.error("GUI configuration failed: %s", exc)
            self.settings = None
            self.setWindowTitle("GAP Pricing Engine — configuration error")
            message = QtWidgets.QLabel(f"Cannot start the GUI:\n{exc}")
            message.setWordWrap(True)
            message.setMargin(24)
            self.setCentralWidget(message)
            return
        self.setWindowTitle("GAP Pricing Engine")
        self.resize(1400, 820)
        self.result = None
        self.basis = None
        self.assump = None
        self._batch_thread = None
        self._startup = None
        self._cb_grid = None
        self._po_grid = None
        self._po_products_visible = None
        self._po_express_override = None
        self._po_margin_override = None
        self._po_rate_basis_override = None
        self._view_prewarm = None
        self._view_prewarm_threads = []
        self._batch_cache: dict[str, tuple] = {}

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        H = QtWidgets.QHBoxLayout(root)
        H.setContentsMargins(0, 0, 0, 0)
        H.setSpacing(0)

        self.nav = QtWidgets.QListWidget()
        self.nav.setFixedWidth(160)
        self.nav.setStyleSheet(NAV_QSS)
        self.nav.addItems(["Single Quote", "Batch", "Category Breakdown", "Pricing Outputs"])
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self.on_nav)
        H.addWidget(self.nav)

        right = QtWidgets.QWidget()
        RV = QtWidgets.QVBoxLayout(right)
        RV.setContentsMargins(12, 12, 12, 12)
        H.addWidget(right, 1)

        self.action_stack = QtWidgets.QStackedWidget()
        self.action_stack.addWidget(self._single_actions())
        self.action_stack.addWidget(self._batch_actions())
        self.cb_split_bar = CategorySplitBar()
        self.cb_split_bar.changed.connect(self._on_split_changed)
        self.action_stack.addWidget(self.cb_split_bar)
        self.action_stack.addWidget(QtWidgets.QWidget())
        RV.addWidget(self.action_stack)

        prow = QtWidgets.QHBoxLayout()
        self.filt = QtWidgets.QLineEdit()
        self.filt.setPlaceholderText("filter (all columns)…")
        self.filt.setFixedWidth(260)
        prow.addStretch()
        prow.addWidget(self.filt)
        self.filter_row_host = QtWidgets.QWidget()
        self.filter_row_host.setLayout(prow)
        RV.addWidget(self.filter_row_host)

        self.batch_model = FastModel()
        self.single_view = SingleQuoteView()
        self.batch_tbl = self._make_table(self.batch_model)
        self.batch_tbl.doubleClicked.connect(self.on_batch_dblclick)

        self.cb_view = ProductPillGridView()
        self.po_table = QtWidgets.QTableWidget()

        self.view_stack = QtWidgets.QStackedWidget()
        self.view_stack.addWidget(self.single_view)
        self.view_stack.addWidget(self.batch_tbl)
        self.view_stack.addWidget(self.cb_view)
        self.view_stack.addWidget(self.po_table)
        RV.addWidget(self.view_stack, 1)

        self.status = self.statusBar()
        self._deb = QtCore.QTimer(self)
        self._deb.setSingleShot(True)
        self._deb.setInterval(250)
        self._deb.timeout.connect(self._apply_filter)
        self.filt.textChanged.connect(lambda _: self._deb.start())

        self._set_busy(True, "loading basis + assumptions…")
        self._startup = StartupThread()
        self._startup.done.connect(self.on_startup_done)
        self._startup.failed.connect(self.on_startup_fail)
        self._startup.start()

        self._startup_data_ready = False
        self._warm_timer = QtCore.QTimer(self)
        self._warm_timer.setInterval(150)
        self._warm_timer.timeout.connect(self._check_fully_ready)
        self._warm_timer.start()

    def _make_table(self, model):
        t = QtWidgets.QTableView()
        t.setModel(model)
        t.setSortingEnabled(True)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setDefaultSectionSize(110)
        t.verticalHeader().setDefaultSectionSize(22)
        return t

    def _single_actions(self):
        w = QtWidgets.QWidget()
        L = QtWidgets.QHBoxLayout(w)
        L.setContentsMargins(0, 0, 0, 0)
        self.browse = QtWidgets.QPushButton("Browse CTR…")
        self.load_db_btn = QtWidgets.QPushButton("Load from DB…")
        self.load_db_btn.setEnabled(self.settings.db_enabled)
        self.load_db_btn.setToolTip(
            "Reconstruct and re-run a past quote directly from the database\n"
            "-- no .ctr file needed. Pick by scheme name/broker/doer/scheme\n"
            "code, not a run ID."
        )
        self.path = QtWidgets.QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("Select a CTR accessible inside the container")
        self.priceb = QtWidgets.QPushButton("Price")
        self.priceb.setObjectName("primaryButton")
        self.saveb = QtWidgets.QPushButton("Save xlsx")
        self.saveb.setEnabled(False)
        self.save_html_btn = QtWidgets.QPushButton("Save HTML")
        self.save_html_btn.setEnabled(False)
        self.save_html_btn.setToolTip(
            "Writes a browsable Category Breakdown + Pricing Outputs HTML\n"
            "report (same rendering as the CB/PO nav tabs). Does not\n"
            "re-write xlsx/parquet if already saved."
        )
        for x, s in ((self.browse, 0), (self.load_db_btn, 0), (self.path, 1),
                     (self.priceb, 0), (self.saveb, 0), (self.save_html_btn, 0)):
            L.addWidget(x, s)
        self.browse.clicked.connect(self.on_browse)
        self.load_db_btn.clicked.connect(self.on_load_from_db)
        self.priceb.clicked.connect(self.on_price)
        self.saveb.clicked.connect(self.on_save)
        self.save_html_btn.clicked.connect(self.on_save_html)
        return w

    def _batch_actions(self):
        w = QtWidgets.QWidget()
        L = QtWidgets.QHBoxLayout(w)
        L.setContentsMargins(0, 0, 0, 0)
        self.bfolder = QtWidgets.QLineEdit(self.settings.input_dir)
        self.bfolder.setToolTip("Mounted input directory inside the container; not a workstation path or S3 URI")
        bf = QtWidgets.QPushButton("CTR folder…")
        bf.clicked.connect(self.pick_folder)
        self.boutput = QtWidgets.QLineEdit(self.settings.output_dir)
        self.boutput.setToolTip("Writable output directory inside the container")
        bo = QtWidgets.QPushButton("Output…")
        bo.clicked.connect(self.pick_output)
        self.workers = QtWidgets.QSpinBox()
        self.workers.setRange(1, max(16, self.settings.pricing_workers))
        self.workers.setValue(self.settings.pricing_workers)
        self.runb = QtWidgets.QPushButton("Run Batch")
        self.runb.setObjectName("primaryButton")
        self.runb.clicked.connect(self.on_batch)
        for x in (QtWidgets.QLabel("CTRs:"), self.bfolder, bf, QtWidgets.QLabel("Out:"), self.boutput, bo,
                  QtWidgets.QLabel("Workers:"), self.workers, self.runb):
            L.addWidget(x)
        L.setStretch(1, 1)
        L.setStretch(4, 1)
        return w

    def on_nav(self, row: int) -> None:
        self.action_stack.setCurrentIndex(row)
        self.view_stack.setCurrentIndex(row)
        self.filter_row_host.setVisible(row == 1)
        if row == 2:
            self._build_cb_view()
        elif row == 3:
            self._build_po_view()
        elif row == 1:
            self._apply_filter()

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        for b in ("browse", "priceb", "runb"):
            if hasattr(self, b):
                getattr(self, b).setEnabled(not busy)
        if msg:
            self.status.showMessage(msg)

    def on_startup_done(self, basis, assump) -> None:
        self.basis = basis
        self.assump = assump
        self._startup_data_ready = True
        self._check_fully_ready()

    def on_startup_fail(self, tb: str) -> None:
        self._warm_timer.stop()
        self._set_busy(False, "STARTUP ERROR")
        QtWidgets.QMessageBox.critical(self, "Startup failed", tb)

    _CLOSE_GRACE_MAX_WAIT_MS = 15000
    _CLOSE_GRACE_POLL_MS = 200

    def closeEvent(self, event) -> None:
        from . import warm_state
        threads = [getattr(self, "_startup", None), getattr(self, "_batch_thread", None)]
        threads.extend(getattr(self, "_view_prewarm_threads", ()))
        self._view_prewarm_threads = [thread for thread in getattr(self, "_view_prewarm_threads", ())
                                      if thread.isRunning()]
        running = [thread for thread in threads if thread is not None and thread.isRunning()]
        if running:
            self.statusBar().showMessage(f"finishing {len(running)} active task(s) before closing...")
            event.ignore()
            QtCore.QTimer.singleShot(self._CLOSE_GRACE_POLL_MS, self.close)
            return
        pending = (warm_state.db_writes_pending() + warm_state.file_syncs_pending()
                   + warm_state.report_writes_pending())
        if pending <= 0:
            event.accept()
            return

        if not hasattr(self, "_close_grace_elapsed_ms"):
            self._close_grace_elapsed_ms = 0
            self.status.showMessage(
                f"finishing {pending} background save(s) before closing..."
            )

        if self._close_grace_elapsed_ms >= self._CLOSE_GRACE_MAX_WAIT_MS:
            event.accept()
            return

        self._close_grace_elapsed_ms += self._CLOSE_GRACE_POLL_MS
        event.ignore()
        QtCore.QTimer.singleShot(self._CLOSE_GRACE_POLL_MS, self.close)

    def _check_fully_ready(self) -> None:
        from . import warm_state
        if not self._startup_data_ready:
            return
        if not (warm_state.UI_WARM_DONE.is_set() and warm_state.ENGINE_WARM_DONE.is_set()):
            self.status.showMessage("finishing background warm-up...")
            return
        self._warm_timer.stop()
        self._set_busy(False, "ready")
    def _build_cb_view(self) -> None:
        if not self.result:
            self.status.showMessage("Price a quote first to view Category Breakdown")
            return
        if self._cb_grid is not None:
            self.cb_view.set_grid(
                self._cb_grid,
                active_products=self.result.active_products,
                active_categories=self.result.ctr_bundle.active_categories,
            )
            self.status.showMessage(f"Category Breakdown: {self.result.scheme_name}")
            return
        self.status.showMessage("building Category Breakdown view…")
        QtWidgets.QApplication.processEvents()
        import openpyxl
        from reporting.category_breakdown import write_category_breakdown
        from reporting.grid_extract import sheet_to_grid
        from reporting.inputs import gather_cb_data

        ctr = self.result.ctr_bundle
        assump = self.result.assump_bundle
        cb_data = gather_cb_data(self.result.per_product_results, ctr.scheme, assump, ctr=ctr)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        write_category_breakdown(wb, cb_data)
        grid = sheet_to_grid(wb["Category Breakdown"], title="Category Breakdown")
        self._cb_grid = grid
        self.cb_view.set_grid(
            grid,
            active_products=self.result.active_products,
            active_categories=self.result.ctr_bundle.active_categories,
        )
        self.status.showMessage(f"Category Breakdown: {self.result.scheme_name}")

    def _on_split_changed(self, new_split: dict) -> None:
        if not self.result:
            return
        self._set_busy(True, "recalculating Category Breakdown split…")
        QtWidgets.QApplication.processEvents()
        try:
            import dataclasses

            import openpyxl
            from reporting.category_breakdown import write_category_breakdown
            from reporting.grid_extract import sheet_to_grid
            from reporting.inputs import gather_cb_data

            ctr = self.result.ctr_bundle
            assump = self.result.assump_bundle
            new_scheme = dataclasses.replace(ctr.scheme, category_split=new_split)
            cb_data = gather_cb_data(self.result.per_product_results, new_scheme, assump, ctr=ctr)
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            write_category_breakdown(wb, cb_data)
            grid = sheet_to_grid(wb["Category Breakdown"], title="Category Breakdown")
            self._cb_grid = grid
            self.cb_view.set_grid(
                grid,
                active_products=self.result.active_products,
                active_categories=self.result.ctr_bundle.active_categories,
            )
            self.status.showMessage(
                f"Category Breakdown: {self.result.scheme_name} (split preview -- not saved to CTR)"
            )
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Split recalculation failed", traceback.format_exc())
        finally:
            self._set_busy(False, "")

    def _build_po_view(self) -> None:
        if not self.result:
            self.status.showMessage("Price a quote first to view Pricing Outputs")
            return
        if self._po_grid is not None:
            populate_table_from_grid(self.po_table, self._po_grid)
            self._install_express_combos()
            self._install_priced_margin_edits()
            self.status.showMessage(f"Pricing Outputs: {self.result.scheme_name}")
            return
        self.status.showMessage("building Pricing Outputs view…")
        QtWidgets.QApplication.processEvents()
        import openpyxl
        from reporting.grid_extract import sheet_to_grid
        from reporting.inputs import gather_po_data
        from reporting.pricing_outputs import write_pricing_outputs

        ctr = self.result.ctr_bundle
        assump = self.result.assump_bundle
        po_data = gather_po_data(self.result.per_product_results, ctr.scheme, assump=assump, ctr=ctr)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        write_pricing_outputs(wb, po_data)
        grid = sheet_to_grid(wb["Pricing Outputs"], title="Pricing Outputs")
        from .po_view import compute_visible_products, filter_po_grid_to_active
        grid = filter_po_grid_to_active(grid, po_data["products"], self.result.active_products)
        self._po_grid = grid
        self._po_products_visible = compute_visible_products(po_data["products"], self.result.active_products)
        populate_table_from_grid(self.po_table, grid)
        self._install_express_combos()
        self._install_priced_margin_edits()
        self.status.showMessage(f"Pricing Outputs: {self.result.scheme_name}")

    def _install_express_combos(self) -> None:
        if not self.result or self._po_grid is None or not self._po_products_visible:
            return
        from .po_view import _EXPRESS_ELIGIBLE_PKEYS, find_express_row
        row = find_express_row(self.po_table)
        if row is None:
            return

        scheme = self.result.ctr_bundle.scheme
        express_map = (
            self._po_express_override
            if self._po_express_override is not None
            else dict(getattr(scheme, "express_over_gla", {}) or {})
        )
        edu_pkey = getattr(scheme, "education_product", None)

        for i, pkey in enumerate(self._po_products_visible):
            col = 1 + i
            if pkey not in _EXPRESS_ELIGIBLE_PKEYS:
                continue
            real_key = edu_pkey if (pkey == "education" and edu_pkey) else pkey
            current = bool(express_map.get(real_key, False))

            underlying = self.po_table.item(row, col)
            if underlying is not None:
                underlying.setText("")

            combo = QtWidgets.QComboBox()
            combo.addItems(["Yes", "No"])
            combo.setCurrentText("Yes" if current else "No")
            combo.setProperty("real_key", real_key)
            combo.setStyleSheet(EXPRESS_COMBO_QSS)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            combo.currentTextChanged.connect(self._on_express_changed)
            self.po_table.setCellWidget(row, col, combo)

    def _on_express_changed(self, _text: str) -> None:
        combo = self.sender()
        if combo is None or not self.result:
            return
        real_key = combo.property("real_key")
        if not real_key:
            return
        new_val = (combo.currentText() == "Yes")

        self._set_busy(True, "recalculating Express over GLA…")
        QtWidgets.QApplication.processEvents()
        try:
            import dataclasses

            import openpyxl
            from reporting.category_breakdown import write_category_breakdown
            from reporting.grid_extract import sheet_to_grid
            from reporting.inputs import gather_cb_data, gather_po_data
            from reporting.pricing_outputs import write_pricing_outputs

            from .po_view import compute_visible_products, filter_po_grid_to_active

            ctr = self.result.ctr_bundle
            assump = self.result.assump_bundle
            scheme = ctr.scheme
            new_express = dict(getattr(scheme, "express_over_gla", {}) or {})
            new_express[real_key] = new_val
            self._po_express_override = new_express
            new_scheme = dataclasses.replace(scheme, express_over_gla=new_express)

            po_data = gather_po_data(self.result.per_product_results, new_scheme, assump=assump, ctr=ctr)
            wb_po = openpyxl.Workbook()
            wb_po.remove(wb_po.active)
            write_pricing_outputs(wb_po, po_data)
            grid_po = sheet_to_grid(wb_po["Pricing Outputs"], title="Pricing Outputs")
            grid_po = filter_po_grid_to_active(grid_po, po_data["products"], self.result.active_products)
            self._po_grid = grid_po
            self._po_products_visible = compute_visible_products(po_data["products"], self.result.active_products)
            populate_table_from_grid(self.po_table, grid_po)
            self._install_express_combos()

            cb_data = gather_cb_data(self.result.per_product_results, new_scheme, assump, ctr=ctr)
            wb_cb = openpyxl.Workbook()
            wb_cb.remove(wb_cb.active)
            write_category_breakdown(wb_cb, cb_data)
            grid_cb = sheet_to_grid(wb_cb["Category Breakdown"], title="Category Breakdown")
            self._cb_grid = grid_cb
            self.cb_view.set_grid(
                grid_cb,
                active_products=self.result.active_products,
                active_categories=self.result.ctr_bundle.active_categories,
            )

            self.status.showMessage(
                f"Pricing Outputs: {self.result.scheme_name} (express-over-GLA preview -- not saved to CTR)"
            )
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Express-over-GLA recalculation failed", traceback.format_exc())
        finally:
            self._set_busy(False, "")

    def _install_priced_margin_edits(self) -> None:
        if not self.result or self._po_grid is None or not self._po_products_visible:
            return
        from .po_view import find_priced_margins_rows, is_priced_margin_editable
        row_map = find_priced_margins_rows(self.po_table)
        if row_map is None:
            return

        scheme = self.result.ctr_bundle.scheme
        edu_pkey = getattr(scheme, "education_product", None)
        override_map = (
            self._po_margin_override
            if self._po_margin_override is not None
            else {k: dict(v) for k, v in (getattr(scheme, "priced_margin_overrides", {}) or {}).items()}
        )
        rate_basis_map = (
            self._po_rate_basis_override
            if self._po_rate_basis_override is not None
            else dict(getattr(scheme, "rate_basis_by_product", {}) or {})
        )

        for field_key, row in row_map.items():
            for i, pkey in enumerate(self._po_products_visible):
                col = 1 + i
                if not is_priced_margin_editable(pkey, field_key):
                    continue
                real_key = edu_pkey if (pkey == "education" and edu_pkey) else pkey

                underlying = self.po_table.item(row, col)
                if underlying is not None:
                    underlying.setText("")

                edit = QtWidgets.QLineEdit()
                edit.setAlignment(QtCore.Qt.AlignCenter)
                edit.setProperty("pkey", pkey)
                edit.setProperty("real_key", real_key)
                edit.setProperty("field_key", field_key)
                edit.setStyleSheet(MARGIN_EDIT_QSS)
                edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

                if field_key == "rate_basis":
                    current_rb = rate_basis_map.get(real_key, 1.0)
                    edit.setText(f"{current_rb * 100:.4g}")
                    edit.editingFinished.connect(self._on_rate_basis_changed)
                else:
                    current = (override_map.get(pkey, {}) or {}).get(field_key)
                    edit.setPlaceholderText("default")
                    if current is not None:
                        edit.setText(f"{current * 100:.4g}")
                    edit.editingFinished.connect(self._on_priced_margin_changed)

                self.po_table.setCellWidget(row, col, edit)

    def _on_priced_margin_changed(self) -> None:
        edit = self.sender()
        if edit is None or not self.result:
            return
        pkey = edit.property("pkey")
        real_key = edit.property("real_key")
        field_key = edit.property("field_key")
        if not pkey or not field_key:
            return

        text = edit.text().strip()
        if text == "":
            new_val = None
        else:
            try:
                new_val = float(text.rstrip("%")) / 100.0
            except ValueError:
                QtWidgets.QMessageBox.warning(self, "Invalid value", f"'{text}' is not a valid percentage.")
                self._install_priced_margin_edits()
                return

        self._set_busy(True, "recalculating Priced Margins…")
        QtWidgets.QApplication.processEvents()
        try:
            import dataclasses

            import openpyxl
            from reporting.grid_extract import sheet_to_grid
            from reporting.inputs import gather_po_data
            from reporting.pricing_outputs import write_pricing_outputs

            from .po_view import compute_visible_products, filter_po_grid_to_active

            ctr = self.result.ctr_bundle
            assump = self.result.assump_bundle
            scheme = ctr.scheme
            new_overrides = {k: dict(v) for k, v in (getattr(scheme, "priced_margin_overrides", {}) or {}).items()}
            product_overrides = dict(new_overrides.get(real_key, {}))
            if new_val is None:
                product_overrides.pop(field_key, None)
            else:
                product_overrides[field_key] = new_val
            new_overrides[real_key] = product_overrides
            self._po_margin_override = new_overrides
            new_scheme = dataclasses.replace(scheme, priced_margin_overrides=new_overrides)
            new_ctr = dataclasses.replace(ctr, scheme=new_scheme)

            po_data = gather_po_data(self.result.per_product_results, new_scheme, assump=assump, ctr=new_ctr)
            wb_po = openpyxl.Workbook()
            wb_po.remove(wb_po.active)
            write_pricing_outputs(wb_po, po_data)
            grid_po = sheet_to_grid(wb_po["Pricing Outputs"], title="Pricing Outputs")
            grid_po = filter_po_grid_to_active(grid_po, po_data["products"], self.result.active_products)
            self._po_grid = grid_po
            self._po_products_visible = compute_visible_products(po_data["products"], self.result.active_products)
            populate_table_from_grid(self.po_table, grid_po)
            self._install_express_combos()
            self._install_priced_margin_edits()

            self.status.showMessage(
                f"Pricing Outputs: {self.result.scheme_name} (priced margins preview -- not saved to CTR)"
            )
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Priced Margins recalculation failed", traceback.format_exc())
        finally:
            self._set_busy(False, "")

    def _on_rate_basis_changed(self) -> None:
        edit = self.sender()
        if edit is None or not self.result:
            return
        pkey = edit.property("pkey")
        real_key = edit.property("real_key")
        if not pkey or not real_key:
            return

        text = edit.text().strip()
        if text == "":
            new_val = 1.0
        else:
            try:
                new_val = float(text.rstrip("%")) / 100.0
            except ValueError:
                QtWidgets.QMessageBox.warning(self, "Invalid value", f"'{text}' is not a valid percentage.")
                self._install_priced_margin_edits()
                return

        self._set_busy(True, "recalculating Rate Basis…")
        QtWidgets.QApplication.processEvents()
        try:
            import dataclasses

            import openpyxl
            from reporting.category_breakdown import write_category_breakdown
            from reporting.grid_extract import sheet_to_grid
            from reporting.inputs import gather_cb_data, gather_po_data
            from reporting.pricing_outputs import write_pricing_outputs

            from .po_view import compute_visible_products, filter_po_grid_to_active

            ctr = self.result.ctr_bundle
            assump = self.result.assump_bundle
            scheme = ctr.scheme
            new_rate_basis = dict(getattr(scheme, "rate_basis_by_product", {}) or {})
            new_rate_basis[real_key] = new_val
            self._po_rate_basis_override = new_rate_basis
            new_scheme = dataclasses.replace(scheme, rate_basis_by_product=new_rate_basis)
            new_ctr = dataclasses.replace(ctr, scheme=new_scheme)

            po_data = gather_po_data(self.result.per_product_results, new_scheme, assump=assump, ctr=new_ctr)
            wb_po = openpyxl.Workbook()
            wb_po.remove(wb_po.active)
            write_pricing_outputs(wb_po, po_data)
            grid_po = sheet_to_grid(wb_po["Pricing Outputs"], title="Pricing Outputs")
            grid_po = filter_po_grid_to_active(grid_po, po_data["products"], self.result.active_products)
            self._po_grid = grid_po
            self._po_products_visible = compute_visible_products(po_data["products"], self.result.active_products)
            populate_table_from_grid(self.po_table, grid_po)
            self._install_express_combos()
            self._install_priced_margin_edits()

            cb_data = gather_cb_data(self.result.per_product_results, new_scheme, assump, ctr=new_ctr)
            wb_cb = openpyxl.Workbook()
            wb_cb.remove(wb_cb.active)
            write_category_breakdown(wb_cb, cb_data)
            grid_cb = sheet_to_grid(wb_cb["Category Breakdown"], title="Category Breakdown")
            self._cb_grid = grid_cb
            self.cb_view.set_grid(
                grid_cb,
                active_products=self.result.active_products,
                active_categories=self.result.ctr_bundle.active_categories,
            )

            self.status.showMessage(
                f"Pricing Outputs: {self.result.scheme_name} "
                f"(rate basis preview -- not saved to CTR; other products' "
                f"commission may shift slightly due to scheme-wide tiering)"
            )
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Rate Basis recalculation failed", traceback.format_exc())
        finally:
            self._set_busy(False, "")

    def _start_view_prewarm(self, res) -> None:
        self._view_prewarm_threads = [thread for thread in self._view_prewarm_threads if thread.isRunning()]
        self._view_prewarm = ViewPrewarmThread(res.ctr_bundle, res.assump_bundle, res.per_product_results)
        self._view_prewarm_threads.append(self._view_prewarm)
        self._view_prewarm.done.connect(self._on_view_prewarm_done)
        self._view_prewarm.failed.connect(lambda tb: None)
        self._view_prewarm.start()

    def _on_view_prewarm_done(self, cb_grid, po_grid, po_products) -> None:
        self._cb_grid = cb_grid
        self._po_grid = po_grid
        from .po_view import compute_visible_products
        self._po_products_visible = compute_visible_products(po_products, self.result.active_products)
        row = self.nav.currentRow()
        if row == 2:
            self.cb_view.set_grid(
                self._cb_grid,
                active_products=self.result.active_products,
                active_categories=self.result.ctr_bundle.active_categories,
            )
            self.status.showMessage(f"Category Breakdown: {self.result.scheme_name}")
        elif row == 3:
            populate_table_from_grid(self.po_table, self._po_grid)
            self._install_express_combos()
            self.status.showMessage(f"Pricing Outputs: {self.result.scheme_name}")

    def _apply_filter(self) -> None:
        if self.view_stack.currentIndex() == 1:
            self.batch_model.set_filter(self.filt.text())

    def on_browse(self) -> None:
        try:
            p, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Select mounted CTR", str(input_directory()), "CTR files (*.ctr *.CTR);;All files (*.*)")
            if p:
                self.path.setText(str(input_file(p)))
                self._db_run_id = None
        except (ConfigurationError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid CTR input", str(exc))

    def on_load_from_db(self) -> None:
        from .quote_runner import list_db_schemes

        try:
            df = list_db_schemes(status_filter="success")
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "DB load failed", traceback.format_exc())
            return

        if df.height == 0:
            QtWidgets.QMessageBox.information(
                self, "No schemes found",
                "No successful runs are currently stored in the database."
            )
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Load Quote from Database")
        dlg.resize(900, 500)
        v = QtWidgets.QVBoxLayout(dlg)

        search = QtWidgets.QLineEdit()
        search.setPlaceholderText("Filter by scheme name, broker, doer, or scheme code…")
        v.addWidget(search)

        cols = ["scheme_name", "scheme_code", "quote_number", "broker_name",
                "doer", "checker", "commencement_date", "started_at", "rerun_number"]
        headers = ["Scheme Name", "Scheme Code", "Quote #", "Broker",
                   "Doer", "Checker", "Commencement", "Run Date", "Rerun #"]

        table = QtWidgets.QTableWidget(df.height, len(cols))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)

        run_ids = df["run_id"].to_list()
        rows_data = df.select(cols).to_dicts()
        for r, row in enumerate(rows_data):
            for c, key in enumerate(cols):
                val = row.get(key)
                table.setItem(r, c, QtWidgets.QTableWidgetItem("" if val is None else str(val)))
        table.resizeColumnsToContents()
        v.addWidget(table)

        def _apply_filter(text: str) -> None:
            text = text.lower().strip()
            for r in range(table.rowCount()):
                if not text:
                    table.setRowHidden(r, False)
                    continue
                hay = " ".join(
                    (table.item(r, c).text() if table.item(r, c) else "")
                    for c in range(table.columnCount())
                ).lower()
                table.setRowHidden(r, text not in hay)

        search.textChanged.connect(_apply_filter)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        v.addWidget(btns)

        table.doubleClicked.connect(lambda _: dlg.accept())

        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return

        selected_rows = table.selectionModel().selectedRows()
        if not selected_rows:
            QtWidgets.QMessageBox.warning(self, "No selection", "Select a scheme/run first.")
            return

        row_idx = selected_rows[0].row()
        chosen_run_id = run_ids[row_idx]
        chosen_label = f"{rows_data[row_idx]['scheme_name']} [Q{rows_data[row_idx]['quote_number']}] (DB)"

        self._db_run_id = chosen_run_id
        self.path.setText(chosen_label)

    def _progress(self, msg) -> None:
        self.status.showMessage(str(msg))
        QtWidgets.QApplication.processEvents()

    def on_price(self) -> None:
        p = self.path.text().strip()
        db_run_id = getattr(self, "_db_run_id", None)
        if not p:
            QtWidgets.QMessageBox.warning(self, "No CTR", "Browse to a CTR first.")
            return
        self._set_busy(True, "pricing…")
        try:
            if db_run_id:
                from .quote_runner import price_quote_from_db
                res = price_quote_from_db(db_run_id, progress_cb=self._progress)
            else:
                from .quote_runner import price_quote
                res = price_quote(p, progress_cb=self._progress)
        except Exception:
            import traceback
            self._set_busy(False, "ERROR")
            QtWidgets.QMessageBox.critical(self, "Pricing failed", traceback.format_exc())
            return
        self._show(res)
        self._set_busy(False, f"priced: {res.scheme_name} [{res.quote_number}]")

    def _show(self, res) -> None:
        self.result = res
        self._cb_grid = None
        self._po_grid = None
        self._po_products_visible = None
        self._po_express_override = None
        self._po_margin_override = None
        self._po_rate_basis_override = None
        self.cb_split_bar.set_values(res.ctr_bundle.scheme.category_split)
        self.single_view.load_result(res)
        self.saveb.setEnabled(True)
        self.save_html_btn.setEnabled(True)
        self._start_view_prewarm(res)

    def on_save(self) -> None:
        if not self.result:
            return
        try:
            from .quote_runner import save_report
            p = save_report(self.result)
            QtWidgets.QMessageBox.information(self, "Saved", f"Report written:\n{p}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))

    def on_save_html(self) -> None:
        if not self.result:
            return
        self.status.showMessage("building HTML report…")
        QtWidgets.QApplication.processEvents()
        try:
            from .quote_runner import save_report
            p = save_report(self.result, write_xlsx=False, write_parquet=False, write_html=True)
            self.status.showMessage(f"HTML report saved: {p.name}")
            QtWidgets.QMessageBox.information(self, "Saved", f"HTML report written:\n{p}")
        except Exception as e:
            self.status.showMessage("HTML save failed")
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))

    def pick_folder(self) -> None:
        try:
            start = input_directory(self.bfolder.text().strip())
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Mounted CTR folder", str(start))
            if d:
                self.bfolder.setText(str(input_directory(d)))
        except (ConfigurationError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid CTR folder", str(exc))

    def pick_output(self) -> None:
        try:
            start = output_directory(self.boutput.text().strip())
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Mounted output folder", str(start))
            if d:
                self.boutput.setText(str(output_directory(d)))
        except (ConfigurationError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid output folder", str(exc))

    def on_batch(self) -> None:
        if self.basis is None:
            QtWidgets.QMessageBox.warning(self, "Not ready", "Snapshots still loading.")
            return
        try:
            folder = input_directory(self.bfolder.text().strip())
            paths = [str(path) for path in batch_ctr_files(folder)]
            destination = output_directory(self.boutput.text().strip())
        except (ConfigurationError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid batch paths", str(exc))
            return
        self.bfolder.setText(str(folder))
        self.boutput.setText(str(destination))
        import time as _time_marker
        self._batch_click_t0 = _time_marker.perf_counter()
        self._batch_cache = {}
        self._set_busy(True, f"batch running — {len(paths)} CTRs…")
        self._batch_thread = BatchThread(
            paths, self.basis, self.assump, self.boutput.text().strip(),
            self.workers.value(), write_report=True,
        )
        self._batch_thread.done.connect(self.on_batch_done)
        self._batch_thread.failed.connect(self.on_batch_fail)
        self._batch_thread.start()

    def on_batch_done(self, df) -> None:
        import time as _time_marker
        _elapsed = _time_marker.perf_counter() - getattr(self, '_batch_click_t0', _time_marker.perf_counter())
        logger.info(f"[UI TIMING] Run Batch click -> table populated: {_elapsed:.2f}s")
        self.batch_model.set_df(df)
        self._apply_filter()
        self._batch_cache = dict(self._batch_thread.results_cache) if self._batch_thread else {}
        self._set_busy(False)
        n_ok = int((df["status"] == "OK").sum()) if "status" in df.columns else df.height
        self.status.showMessage(
            f"batch done — {n_ok}/{df.height} OK  "
            f"(double-click a row to view Category Breakdown / Pricing Outputs / Save HTML)"
        )

    def on_batch_fail(self, tb: str) -> None:
        self._set_busy(False, "BATCH ERROR")
        QtWidgets.QMessageBox.critical(self, "Batch failed", tb)

    def on_batch_dblclick(self, ix) -> None:
        cols = self.batch_model._cols
        if "ctr_name" not in cols:
            return
        ctr_name = self.batch_model._rows[ix.row()][cols.index("ctr_name")]
        if not ctr_name:
            return
        match = None
        for full_path in self._batch_cache:
            if Path(full_path).name == ctr_name:
                match = full_path
                break
        if match is None:
            self.status.showMessage(
                f"No cached pricing data for {ctr_name} (it may have failed or "
                f"the batch cache was cleared by a newer run)."
            )
            return
        ctr, assump, all_results = self._batch_cache[match]
        dlg = BatchDetailDialog(ctr, assump, all_results, match,
                                 out_dir=self.boutput.text().strip(), parent=self)
        dlg.exec()


def run() -> int:
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QtWidgets.QApplication([])

    from .theme import apply_light_theme
    apply_light_theme(app)

    win = GapWindow()
    win.show()
    if owns_app:
        return app.exec()
    return 0


if __name__ == "__main__":
    run()