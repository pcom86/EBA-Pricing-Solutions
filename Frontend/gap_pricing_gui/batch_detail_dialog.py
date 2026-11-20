from __future__ import annotations

import time

import polars as pl
from PySide6 import QtWidgets

from .grid_view import ProductPillGridView, populate_table_from_grid
from .single_quote_view import populate_summary_table, product_display_name
from .theme import OM, SUMMARY_TABLE_QSS

NUMERIC_COLS = {"Members", "Covered", "Total Annual Premium", "Avg Premium (Covered)", "Profit Margin"}
CURRENCY_COLS = {"Total Annual Premium", "Avg Premium (Covered)", "Profit Margin"}


def build_batch_product_summary_df(active_products, per_product_results) -> pl.DataFrame:
    rows = []
    for pkey in active_products:
        df = per_product_results[pkey]
        has_prem = "total_annual_premium" in df.columns
        has_margin = "profit_margin_amount" in df.columns

        total_members = df.height
        covered = df.filter(pl.col("total_annual_premium") > 0).height if has_prem else total_members
        total_premium = float(df["total_annual_premium"].sum()) if has_prem else 0.0
        total_margin = float(df["profit_margin_amount"].sum()) if has_margin else 0.0
        avg_premium = (total_premium / covered) if covered else 0.0

        rows.append({
            "Product": pkey,
            "Product Name": product_display_name(pkey),
            "Members": total_members,
            "Covered": covered,
            "Total Annual Premium": round(total_premium, 2),
            "Avg Premium (Covered)": round(avg_premium, 2),
            "Profit Margin": round(total_margin, 2),
            "_raw_premium": total_premium,
        })

    scheme_total = sum(r["_raw_premium"] for r in rows) or 1.0
    for r in rows:
        r["% of Scheme Premium"] = round(r["_raw_premium"] / scheme_total * 100.0, 2)
        del r["_raw_premium"]

    return pl.DataFrame(rows)


class BatchDetailDialog(QtWidgets.QDialog):

    def __init__(self, ctr, assump, all_results: dict[str, pl.DataFrame],
                 ctr_path: str, out_dir: str | None = None, parent=None):
        super().__init__(parent)
        self.ctr = ctr
        self.assump = assump
        self.all_results = all_results
        self.ctr_path = ctr_path
        self.out_dir = out_dir
        self.active_products = list(all_results.keys())

        self._cb_grid = None
        self._po_grid = None

        scheme = ctr.scheme
        self.setWindowTitle(f"{scheme.scheme_name} — Quote {scheme.quote_number}")
        self.resize(1300, 650)

        V = QtWidgets.QVBoxLayout(self)
        V.addWidget(self._build_header(scheme))

        nav_row = QtWidgets.QHBoxLayout()
        self.btn_summary = QtWidgets.QPushButton("Summary")
        self.btn_cb = QtWidgets.QPushButton("Category Breakdown")
        self.btn_po = QtWidgets.QPushButton("Pricing Outputs")
        for b in (self.btn_summary, self.btn_cb, self.btn_po):
            b.setCheckable(True)
        self.btn_summary.setChecked(True)
        self._nav_group = QtWidgets.QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for b in (self.btn_summary, self.btn_cb, self.btn_po):
            self._nav_group.addButton(b)
        self.btn_summary.clicked.connect(lambda: self._show_tab(0))
        self.btn_cb.clicked.connect(lambda: self._show_tab(1))
        self.btn_po.clicked.connect(lambda: self._show_tab(2))
        nav_row.addWidget(self.btn_summary)
        nav_row.addWidget(self.btn_cb)
        nav_row.addWidget(self.btn_po)
        nav_row.addStretch()
        self.save_html_btn = QtWidgets.QPushButton("Save HTML")
        self.save_html_btn.clicked.connect(self.on_save_html)
        nav_row.addWidget(self.save_html_btn)
        V.addLayout(nav_row)

        self.summary_table = QtWidgets.QTableWidget()
        self.summary_table.setStyleSheet(SUMMARY_TABLE_QSS)
        populate_summary_table(
            self.summary_table,
            build_batch_product_summary_df(self.active_products, self.all_results),
        )

        self.cb_view = ProductPillGridView()
        self.po_table = QtWidgets.QTableWidget()
        self.po_table.setStyleSheet(SUMMARY_TABLE_QSS)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.summary_table)
        self.stack.addWidget(self.cb_view)
        self.stack.addWidget(self.po_table)
        V.addWidget(self.stack, 1)

        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet(f"color:{OM['muted']}; padding:4px;")
        V.addWidget(self.status)

    def _build_header(self, scheme) -> QtWidgets.QWidget:
        n_members = self.all_results[self.active_products[0]].height if self.active_products else 0
        container = QtWidgets.QWidget()
        Vh = QtWidgets.QVBoxLayout(container)
        Vh.setContentsMargins(4, 4, 4, 8)
        Vh.setSpacing(2)

        line1 = QtWidgets.QLabel(
            f"<span style='font-size:15px;font-weight:700;color:{OM['ink']};'>{scheme.scheme_name}</span>"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;Quote No. {scheme.quote_number}"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;{len(self.active_products)} products"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;{n_members:,} members"
        )
        line1.setStyleSheet(f"color:{OM['muted']}; font-size:13px;")
        Vh.addWidget(line1)

        rule = QtWidgets.QFrame()
        rule.setFrameShape(QtWidgets.QFrame.HLine)
        rule.setStyleSheet(f"color:{OM['border']};")
        Vh.addWidget(rule)
        return container

    def _show_tab(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        if idx == 1:
            self._ensure_cb_built()
        elif idx == 2:
            self._ensure_po_built()

    def _ensure_cb_built(self) -> None:
        if self._cb_grid is not None:
            return
        self.status.setText("building Category Breakdown…")
        QtWidgets.QApplication.processEvents()
        import openpyxl
        from reporting.category_breakdown import write_category_breakdown
        from reporting.grid_extract import sheet_to_grid
        from reporting.inputs import gather_cb_data

        cb_data = gather_cb_data(self.all_results, self.ctr.scheme, self.assump, ctr=self.ctr)
        wb = openpyxl.Workbook(); wb.remove(wb.active)
        write_category_breakdown(wb, cb_data)
        grid = sheet_to_grid(wb["Category Breakdown"], title="Category Breakdown")
        self._cb_grid = grid
        self.cb_view.set_grid(
            grid,
            active_products=getattr(self.ctr, "active_products", None),
            active_categories=getattr(self.ctr, "active_categories", None),
        )
        self.status.setText("")

    def _ensure_po_built(self) -> None:
        if self._po_grid is not None:
            return
        self.status.setText("building Pricing Outputs…")
        QtWidgets.QApplication.processEvents()
        import openpyxl
        from reporting.grid_extract import sheet_to_grid
        from reporting.inputs import gather_po_data
        from reporting.pricing_outputs import write_pricing_outputs

        po_data = gather_po_data(self.all_results, self.ctr.scheme, assump=self.assump, ctr=self.ctr)
        wb = openpyxl.Workbook(); wb.remove(wb.active)
        write_pricing_outputs(wb, po_data)
        grid = sheet_to_grid(wb["Pricing Outputs"], title="Pricing Outputs")
        from .po_view import filter_po_grid_to_active
        grid = filter_po_grid_to_active(
            grid, po_data["products"], getattr(self.ctr, "active_products", None)
        )
        self._po_grid = grid
        populate_table_from_grid(self.po_table, grid)
        self.status.setText("")

    def on_save_html(self) -> None:
        self.status.setText("saving HTML report…")
        QtWidgets.QApplication.processEvents()
        try:
            from reporting import write_pricing_report

            from .file_access import output_directory
            out_dir = output_directory(self.out_dir)
            scheme = self.ctr.scheme
            stamp = time.strftime("%Y%m%d_%H%M%S")
            safe_scheme = "".join(c if c.isalnum() else "_" for c in (scheme.scheme_name or "scheme"))
            workbook_path = out_dir / f"{safe_scheme}_Q{scheme.quote_number or 'X'}_{stamp}.xlsx"

            write_pricing_report(
                workbook_path = workbook_path,
                all_results   = self.all_results,
                ctr           = self.ctr,
                assump        = self.assump,
                ctr_path      = self.ctr_path,
                write_xlsx    = False,
                write_parquet = False,
                write_html    = True,
            )
            html_path = workbook_path.with_suffix(".html")
            self.status.setText(f"HTML report saved: {html_path.name}")
            QtWidgets.QMessageBox.information(self, "Saved", f"HTML report written:\n{html_path}")
        except Exception as e:  # noqa: BLE001 - surface any save failure to the user
            self.status.setText("HTML save failed")
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))
