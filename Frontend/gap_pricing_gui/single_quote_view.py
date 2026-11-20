from __future__ import annotations

import polars as pl
from PySide6 import QtCore, QtGui, QtWidgets

from .models import FastModel
from .theme import OM, SUMMARY_TABLE_QSS

PRODUCT_NAMES = {
    "gla": "GLA",
    "lsdb": "LSDB",
    "ci": "CI",
    "spouse_gla": "Spouse GLA",
    "spouse_lsdb": "Spouse LSDB",
    "terminal": "Terminal Illness",
    "accident_death": "Accidental Death",
    "accident_disability": "Accidental Disability",
    "dismemberment": "Dismemberment",
    "fc": "Family Cover (FC)",
    "gip": "GIP",
    "tip": "TIP",
    "education_guardian": "Education Guardian",
    "ext_fc": "Extended Family Cover",
}

NUMERIC_COLS = {"Members", "Covered", "Total Annual Premium", "Avg Premium (Covered)", "Profit Margin"}
CURRENCY_COLS = {"Total Annual Premium", "Avg Premium (Covered)", "Profit Margin"}


def product_display_name(pkey: str) -> str:
    return PRODUCT_NAMES.get(pkey, pkey.replace("_", " ").title())


def build_product_summary_df(res) -> pl.DataFrame:
    total_scheme_premium = res.aggregates.get("total_annual_premium", 0) or 1.0
    rows = []
    for pkey in res.active_products:
        df = res.per_product_results[pkey]
        has_prem = "total_annual_premium" in df.columns
        has_margin = "profit_margin_amount" in df.columns

        total_members = df.height
        covered = df.filter(pl.col("total_annual_premium") > 0).height if has_prem else total_members
        total_premium = float(df["total_annual_premium"].sum()) if has_prem else 0.0
        total_margin = float(df["profit_margin_amount"].sum()) if has_margin else 0.0
        avg_premium = (total_premium / covered) if covered else 0.0
        pct_of_scheme = (total_premium / total_scheme_premium * 100.0) if total_scheme_premium else 0.0

        rows.append({
            "Product": pkey,
            "Product Name": product_display_name(pkey),
            "Members": total_members,
            "Covered": covered,
            "Total Annual Premium": round(total_premium, 2),
            "Avg Premium (Covered)": round(avg_premium, 2),
            "Profit Margin": round(total_margin, 2),
            "% of Scheme Premium": round(pct_of_scheme, 2),
        })
    return pl.DataFrame(rows)


def _fmt(col: str, val) -> str:
    if col in CURRENCY_COLS:
        return f"R {val:,.2f}"
    if col == "% of Scheme Premium":
        return f"{val:.2f}%"
    if col in NUMERIC_COLS:
        return f"{val:,}"
    return str(val)


def populate_summary_table(table: QtWidgets.QTableWidget, df: pl.DataFrame) -> None:
    display_cols = [c for c in df.columns if c != "Product"]
    table.clear()
    table.setSortingEnabled(False)
    table.setRowCount(df.height + 1)
    table.setColumnCount(len(display_cols))
    table.setHorizontalHeaderLabels(display_cols)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)

    right_align_cols = NUMERIC_COLS | {"% of Scheme Premium"}

    for r in range(df.height):
        for c, col in enumerate(display_cols):
            val = df[col][r]
            item = QtWidgets.QTableWidgetItem(_fmt(col, val))
            if col in right_align_cols:
                item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            table.setItem(r, c, item)

    total_row = df.height
    totals = {
        "Product Name": "TOTAL",
        "Members": df["Members"].sum(),
        "Covered": df["Covered"].sum(),
        "Total Annual Premium": df["Total Annual Premium"].sum(),
        "Avg Premium (Covered)": None,
        "Profit Margin": df["Profit Margin"].sum(),
        "% of Scheme Premium": 100.0,
    }
    for c, col in enumerate(display_cols):
        val = totals.get(col)
        text = "" if val is None else _fmt(col, val)
        item = QtWidgets.QTableWidgetItem(text)
        f = item.font(); f.setBold(True); item.setFont(f)
        item.setBackground(QtGui.QBrush(QtGui.QColor(OM["panel"])))
        if col in right_align_cols:
            item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        table.setItem(total_row, c, item)

    table.setSortingEnabled(True)
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)


class SingleQuoteView(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self._view_mode = "summary"
        self._product_keys: list[str] = []

        V = QtWidgets.QVBoxLayout(self)
        V.setContentsMargins(0, 0, 0, 0)

        self.summary_container = QtWidgets.QWidget()
        self.summary_layout = QtWidgets.QVBoxLayout(self.summary_container)
        self.summary_layout.setContentsMargins(4, 8, 4, 8)
        self.summary_layout.setSpacing(6)
        V.addWidget(self.summary_container)

        nav_row = QtWidgets.QHBoxLayout()
        self.back_btn = QtWidgets.QPushButton("◀ Back to Summary")
        self.back_btn.setVisible(False)
        self.back_btn.clicked.connect(self.on_back_to_summary)
        nav_row.addWidget(self.back_btn)

        self.product_label = QtWidgets.QLabel("Product:")
        self.product_label.setVisible(False)
        self.product_combo = QtWidgets.QComboBox()
        self.product_combo.setVisible(False)
        self.product_combo.currentIndexChanged.connect(self._on_combo_changed)
        nav_row.addWidget(self.product_label)
        nav_row.addWidget(self.product_combo)

        self.parquet_btn = QtWidgets.QPushButton("Full Data (Parquet) ▸")
        self.parquet_btn.setVisible(False)
        self.parquet_btn.clicked.connect(self.on_show_parquet)
        nav_row.addWidget(self.parquet_btn)

        nav_row.addStretch()
        V.addLayout(nav_row)

        self.parquet_controls = QtWidgets.QWidget()
        PC = QtWidgets.QHBoxLayout(self.parquet_controls)
        PC.setContentsMargins(0, 4, 0, 4)
        self.parquet_product_label = QtWidgets.QLabel("Product:")
        self.parquet_product_combo = QtWidgets.QComboBox()
        self.parquet_product_combo.currentIndexChanged.connect(self._on_parquet_product_changed)
        self.parquet_filter = QtWidgets.QLineEdit()
        self.parquet_filter.setPlaceholderText("filter (all columns)…")
        self.parquet_filter.setFixedWidth(260)
        self.parquet_filter.textChanged.connect(self._on_parquet_filter_changed)
        self.parquet_row_count = QtWidgets.QLabel("")
        self.parquet_row_count.setStyleSheet(f"color:{OM['muted']}; font-size:11px;")
        PC.addWidget(self.parquet_product_label)
        PC.addWidget(self.parquet_product_combo)
        PC.addWidget(self.parquet_filter)
        PC.addStretch()
        PC.addWidget(self.parquet_row_count)
        self.parquet_controls.setVisible(False)
        V.addWidget(self.parquet_controls)

        self.table = QtWidgets.QTableWidget()
        self.table.setStyleSheet(SUMMARY_TABLE_QSS)
        self.table.doubleClicked.connect(self.on_dblclick)

        self.parquet_model = FastModel()
        self.parquet_table = QtWidgets.QTableView()
        self.parquet_table.setModel(self.parquet_model)
        self.parquet_table.setSortingEnabled(True)
        self.parquet_table.setAlternatingRowColors(True)
        self.parquet_table.horizontalHeader().setDefaultSectionSize(110)
        self.parquet_table.verticalHeader().setDefaultSectionSize(22)

        self.content_stack = QtWidgets.QStackedWidget()
        self.content_stack.addWidget(self.table)
        self.content_stack.addWidget(self.parquet_table)
        V.addWidget(self.content_stack, 1)

        self.status = QtWidgets.QLabel("Price a quote to see the summary.")
        self.status.setStyleSheet(f"color:{OM['muted']}; padding:4px;")
        V.addWidget(self.status)

    def load_result(self, res) -> None:
        self.result = res
        self._product_keys = list(res.active_products)
        self._parquet_df = None
        self._rebuild_summary_line(res)
        self._rebuild_product_combo()
        self._rebuild_parquet_product_combo()
        self.parquet_btn.setVisible(True)
        self._show_summary()

    def reset(self) -> None:
        self.result = None
        self._product_keys = []
        self._parquet_df = None
        while self.summary_layout.count():
            item = self.summary_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.back_btn.setVisible(False)
        self.product_combo.setVisible(False)
        self.product_label.setVisible(False)
        self.parquet_btn.setVisible(False)
        self.parquet_controls.setVisible(False)
        self.parquet_filter.blockSignals(True)
        self.parquet_filter.clear()
        self.parquet_filter.blockSignals(False)
        self.parquet_model.set_df(pl.DataFrame())
        self.content_stack.setCurrentWidget(self.table)
        self.status.setText("Price a quote to see the summary.")

    def _clear_layout_recursive(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
                continue
            child_layout = item.layout()
            if child_layout is not None:
                self._clear_layout_recursive(child_layout)
                child_layout.deleteLater()

    def _rebuild_summary_line(self, res) -> None:
        self._clear_layout_recursive(self.summary_layout)

        scheme = res.ctr_bundle.scheme
        n_members = res.per_product_results[res.active_products[0]].height if res.active_products else 0

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(32)
        grid.setVerticalSpacing(2)

        fields = [
            ("Scheme", res.scheme_name),
            ("Quote No.", str(res.quote_number)),
            ("Quote Type", getattr(scheme, "quote_type", "—")),
            ("Products", f"{len(res.active_products)} active"),
            ("Members", f"{n_members:,}"),
            ("Total Annual Premium", f"R {res.aggregates.get('total_annual_premium', 0):,.2f}"),
            ("Total Gross Risk Premium", f"R {res.aggregates.get('total_gross_risk_premium', 0):,.2f}"),
            ("Total Cover", f"R {res.aggregates.get('total_cover', 0):,.0f}"),
        ]
        for col, (label, value) in enumerate(fields):
            lab = QtWidgets.QLabel(label)
            lab.setStyleSheet(f"color:{OM['muted']}; font-size:11px; background:transparent; border:none;")
            val = QtWidgets.QLabel(value)
            val.setStyleSheet(f"color:{OM['ink']}; font-size:14px; font-weight:600; background:transparent; border:none;")
            box = QtWidgets.QVBoxLayout()
            box.setSpacing(0)
            box.addWidget(lab)
            box.addWidget(val)
            grid.addLayout(box, 0, col)

        self.summary_layout.addLayout(grid)

        rule = QtWidgets.QFrame()
        rule.setFrameShape(QtWidgets.QFrame.HLine)
        rule.setStyleSheet(f"color:{OM['border']};")
        self.summary_layout.addWidget(rule)

    def _rebuild_product_combo(self) -> None:
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        for pkey in self._product_keys:
            self.product_combo.addItem(product_display_name(pkey), userData=pkey)
        self.product_combo.blockSignals(False)

    def _rebuild_parquet_product_combo(self) -> None:
        self.parquet_product_combo.blockSignals(True)
        self.parquet_product_combo.clear()
        self.parquet_product_combo.addItem("All Products", userData=None)
        for pkey in self._product_keys:
            self.parquet_product_combo.addItem(product_display_name(pkey), userData=pkey)
        self.parquet_product_combo.blockSignals(False)

    def _show_summary(self) -> None:
        self._view_mode = "summary"
        populate_summary_table(self.table, build_product_summary_df(self.result))
        self.content_stack.setCurrentWidget(self.table)
        self.back_btn.setVisible(False)
        self.product_combo.setVisible(False)
        self.product_label.setVisible(False)
        self.parquet_controls.setVisible(False)
        self.status.setText(
            f"Summary — {self.result.scheme_name}  (double-click a row for member-level detail)"
        )

    def _show_product_raw(self, pkey: str) -> None:
        if not self.result or not pkey or pkey not in self.result.per_product_results:
            return
        self._view_mode = pkey
        self.content_stack.setCurrentWidget(self.table)
        self.parquet_controls.setVisible(False)
        df = self.result.per_product_results[pkey]
        cols = df.columns[:12]

        table = self.table
        table.setSortingEnabled(False)
        table.clear()
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setRowCount(df.height)
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        for r in range(df.height):
            for c, col in enumerate(cols):
                table.setItem(r, c, QtWidgets.QTableWidgetItem(str(df[col][r])))
        table.setSortingEnabled(True)
        table.resizeColumnsToContents()

        self.back_btn.setVisible(True)
        self.product_combo.setVisible(True)
        self.product_label.setVisible(True)
        idx = self._product_keys.index(pkey)
        if self.product_combo.currentIndex() != idx:
            self.product_combo.blockSignals(True)
            self.product_combo.setCurrentIndex(idx)
            self.product_combo.blockSignals(False)
        self.status.setText(
            f"{product_display_name(pkey)} — member-level detail, first 12 cols "
            f"({self.result.scheme_name})"
        )

    def _build_parquet_frame(self):
        if self._parquet_df is None and self.result is not None:
            from reporting.meta_engine import build_engine_frame
            self._parquet_df = build_engine_frame(self.result.per_product_results)
        return self._parquet_df

    def on_show_parquet(self) -> None:
        if not self.result:
            return
        self._view_mode = "parquet"
        df = self._build_parquet_frame()
        self.content_stack.setCurrentWidget(self.parquet_table)
        self.back_btn.setVisible(True)
        self.product_combo.setVisible(False)
        self.product_label.setVisible(False)
        self.parquet_controls.setVisible(True)
        self._apply_parquet_view()
        if df is not None:
            self.status.setText(
                f"Full Data (Parquet) — {self.result.scheme_name}  "
                f"({df.height:,} rows x {df.width} cols, all products stacked)"
            )

    def _on_parquet_product_changed(self, i: int) -> None:
        if i < 0:
            return
        self._apply_parquet_view()

    def _on_parquet_filter_changed(self, _text: str) -> None:
        self._apply_parquet_view()

    def _apply_parquet_view(self) -> None:
        df = self._build_parquet_frame()
        if df is None:
            return
        pkey = self.parquet_product_combo.currentData()
        view_df = df.filter(pl.col("product") == pkey) if pkey else df
        self.parquet_model.set_df(view_df)
        text = self.parquet_filter.text()
        if text:
            self.parquet_model.set_filter(text)
        self.parquet_row_count.setText(f"{self.parquet_model.rowCount():,} rows shown")

    def on_dblclick(self, ix) -> None:
        if self._view_mode != "summary" or not self.result:
            return
        row = ix.row()
        if row >= len(self._product_keys):
            return
        clicked_name = self.table.item(row, 0).text()
        for pkey in self._product_keys:
            if product_display_name(pkey) == clicked_name:
                self._show_product_raw(pkey)
                break

    def _on_combo_changed(self, i: int) -> None:
        if i < 0:
            return
        pkey = self.product_combo.itemData(i)
        if pkey:
            self._show_product_raw(pkey)

    def on_back_to_summary(self) -> None:
        if self.result:
            self._show_summary()
