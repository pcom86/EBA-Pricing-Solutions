"""Headless (QT_QPA_PLATFORM=offscreen) coverage tests for the Qt-facing
gap_pricing_gui modules: theme, models, workers, category_split_bar,
grid_view, po_view, single_quote_view, batch_detail_dialog, main_window,
and launch_gap_gui.main().
"""
from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import polars as pl
import pytest
from gap_config import ConfigurationError, load_settings
from PySide6 import QtCore, QtGui, QtWidgets


@pytest.fixture(scope="session")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    QtWidgets.QApplication.processEvents()
    assert predicate(), "timed out waiting for Qt condition"


def _make_settings(tmp_path: Path, environ: dict | None = None):
    (tmp_path / "config.json").write_text(json.dumps({
        "paths": {
            "input": "input",
            "output": "output",
            "basis": "basis",
            "assumptions": "assumptions",
            "cache": "cache",
            "staging": "staging",
            "artifacts": "artifacts",
        },
    }), encoding="utf-8")
    for name in ("input", "basis", "assumptions"):
        (tmp_path / name).mkdir(exist_ok=True)
    return load_settings(tmp_path / "config.json", environ or {})


def _cell(display="", *, bold=False, fill=None, font_color="000000",
          align="left", value=None):
    from reporting.grid_extract import GridCell
    return GridCell(value=value, display=display, bold=bold,
                    font_color=font_color, fill=fill, align=align)


# ---------------------------------------------------------------------------
# theme
# ---------------------------------------------------------------------------

def test_apply_light_theme(qapp):
    from gap_pricing_gui import theme
    theme.apply_light_theme(qapp)
    assert "QPushButton" in qapp.styleSheet()
    pal = qapp.palette()
    assert pal.color(QtGui.QPalette.Highlight).name() == theme.OM["green"].lower()
    assert pal.color(QtGui.QPalette.Window).name() == theme.OM["panel"].lower()


# ---------------------------------------------------------------------------
# models.FastModel
# ---------------------------------------------------------------------------

@pytest.fixture
def fast_model(qapp):
    from gap_pricing_gui.models import FastModel
    df = pl.DataFrame({
        "name": ["b-row", "a-row", "c-row"],
        "amount": [2000.5, None, 1000.0],
        "count": [3, 1, 2000],
    })
    return FastModel(df)


class TestFastModel:
    def test_counts(self, fast_model):
        assert fast_model.rowCount() == 3
        assert fast_model.columnCount() == 3
        # valid (child) index -> 0; only root index reports real counts
        child = fast_model.index(0, 0)
        assert fast_model.rowCount(child) == 0
        assert fast_model.columnCount(child) == 0

    def test_data_display(self, fast_model):
        ix = fast_model.index(0, 1)
        assert fast_model.data(ix) == "2,000.50"          # float
        assert fast_model.data(fast_model.index(1, 1)) == ""  # None -> ""
        assert fast_model.data(fast_model.index(0, 0)) == "b-row"
        assert fast_model.data(fast_model.index(2, 2)) == "2,000"  # int
        assert fast_model.data(QtCore.QModelIndex()) is None
        assert fast_model.data(ix, QtCore.Qt.UserRole) is None

    def test_data_alignment(self, fast_model):
        align = fast_model.data(fast_model.index(0, 1),
                                QtCore.Qt.TextAlignmentRole)
        assert align == int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        assert fast_model.data(fast_model.index(0, 0),
                               QtCore.Qt.TextAlignmentRole) is None

    def test_header(self, fast_model):
        assert fast_model.headerData(1, QtCore.Qt.Horizontal) == "amount"
        assert fast_model.headerData(2, QtCore.Qt.Vertical) == 3
        assert fast_model.headerData(0, QtCore.Qt.Horizontal,
                                     QtCore.Qt.UserRole) is None

    def test_sort(self, fast_model):
        fast_model.sort(1, QtCore.Qt.AscendingOrder)
        names = [fast_model._rows[i][0] for i in range(3)]
        assert names == ["c-row", "b-row", "a-row"]  # nulls last
        fast_model.sort(1, QtCore.Qt.DescendingOrder)
        assert fast_model._rows[0][0] == "b-row"
        assert fast_model._rows[2][1] is None

    def test_sort_empty_noop(self, qapp):
        from gap_pricing_gui.models import FastModel
        m = FastModel()
        m.sort(0, QtCore.Qt.AscendingOrder)  # must not raise
        assert m.rowCount() == 0

    def test_filter(self, fast_model):
        fast_model.set_filter("a-row")
        assert fast_model.rowCount() == 1
        assert fast_model._rows[0][0] == "a-row"
        fast_model.set_filter("")
        assert fast_model.rowCount() == 3

    def test_set_df(self, fast_model):
        fast_model.set_df(pl.DataFrame({"x": [1, 2]}))
        assert fast_model.rowCount() == 2
        assert fast_model.columnCount() == 1
        fast_model.set_filter("9")
        assert fast_model.rowCount() == 0


# ---------------------------------------------------------------------------
# workers
# ---------------------------------------------------------------------------

class TestWorkers:
    def test_startup_thread_done(self, qapp, monkeypatch):
        import pricing_engine.concurrent_runner as cr
        from gap_pricing_gui import workers
        monkeypatch.setattr(workers, "snapshot_folders",
                            lambda: ("basis_dir", "assump_dir"))
        monkeypatch.setattr(cr, "load_shared_snapshots",
                            lambda b, a: ("B", "A"))
        t = workers.StartupThread()
        got = []
        t.done.connect(lambda *a: got.append(a))
        t.failed.connect(lambda tb: got.append(("fail", tb)))
        t.run()
        assert got == [("B", "A")]

    def test_startup_thread_failure(self, qapp, monkeypatch):
        import pricing_engine.concurrent_runner as cr
        from gap_pricing_gui import workers
        monkeypatch.setattr(workers, "snapshot_folders", lambda: ("b", "a"))

        def boom(b, a):
            raise RuntimeError("snapshots missing")

        monkeypatch.setattr(cr, "load_shared_snapshots", boom)
        t = workers.StartupThread()
        got = []
        t.done.connect(lambda *a: got.append(a))
        t.failed.connect(got.append)
        t.run()
        assert len(got) == 1 and "snapshots missing" in got[0]

    def test_batch_thread_done_and_sink(self, qapp, monkeypatch):
        import pricing_engine.concurrent_runner as cr
        from gap_pricing_gui import workers
        summary = pl.DataFrame({"ctr_name": ["a.ctr"], "status": ["OK"]})

        def fake_run(paths, basis, assump, out_dir, **kw):
            assert paths == ["p1.ctr"]
            kw["result_sink"](paths[0], "ctr", "assump", {"gla": "df"})
            return summary

        monkeypatch.setattr(cr, "run_quotes_concurrent", fake_run)
        t = workers.BatchThread(["p1.ctr"], "B", "A", "out", 2,
                                write_report=True)
        got = []
        t.done.connect(got.append)
        t.failed.connect(got.append)
        t.run()
        assert len(got) == 1 and got[0] is summary
        assert t.results_cache["p1.ctr"] == ("ctr", "assump", {"gla": "df"})

    def test_batch_thread_failure(self, qapp, monkeypatch):
        import pricing_engine.concurrent_runner as cr
        from gap_pricing_gui import workers

        def boom(*a, **k):
            raise RuntimeError("batch boom")

        monkeypatch.setattr(cr, "run_quotes_concurrent", boom)
        t = workers.BatchThread([], "B", "A", "out", 1)
        got = []
        t.done.connect(got.append)
        t.failed.connect(got.append)
        t.run()
        assert len(got) == 1 and "batch boom" in got[0]

    def _patch_reporting_fakes(self, monkeypatch):
        import reporting.category_breakdown as rcb
        import reporting.inputs as ri
        import reporting.pricing_outputs as rpo
        monkeypatch.setattr(ri, "gather_cb_data", lambda *a, **k: {"cb": 1})
        monkeypatch.setattr(ri, "gather_po_data",
                            lambda *a, **k: {"products": []})
        monkeypatch.setattr(
            rcb, "write_category_breakdown",
            lambda wb, data: wb.create_sheet("Category Breakdown")
            .__setitem__("A1", "cb"))
        monkeypatch.setattr(
            rpo, "write_pricing_outputs",
            lambda wb, data: wb.create_sheet("Pricing Outputs")
            .__setitem__("A1", "po"))

    def test_view_prewarm_done(self, qapp, monkeypatch):
        from gap_pricing_gui import workers
        self._patch_reporting_fakes(monkeypatch)
        ctr = SimpleNamespace(scheme=SimpleNamespace(), active_products=[])
        t = workers.ViewPrewarmThread(ctr, object(), {})
        got = []
        t.done.connect(lambda *a: got.append(a))
        t.failed.connect(lambda tb: got.append(("fail", tb)))
        t.run()
        assert len(got) == 1
        cb_grid, po_grid, products = got[0]
        assert cb_grid.title == "Category Breakdown"
        assert po_grid.title == "Pricing Outputs"
        assert products == []

    def test_view_prewarm_failure(self, qapp, monkeypatch):
        import reporting.inputs as ri
        from gap_pricing_gui import workers
        monkeypatch.setattr(
            ri, "gather_cb_data",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cb fail")))
        ctr = SimpleNamespace(scheme=SimpleNamespace(), active_products=[])
        t = workers.ViewPrewarmThread(ctr, object(), {})
        got = []
        t.done.connect(lambda *a: got.append(a))
        t.failed.connect(got.append)
        t.run()
        assert len(got) == 1 and "cb fail" in got[0]


# ---------------------------------------------------------------------------
# category_split_bar
# ---------------------------------------------------------------------------

class TestCategorySplitBar:
    def test_defaults_and_set_values(self, qapp):
        from gap_pricing_gui.category_split_bar import CategorySplitBar
        bar = CategorySplitBar()
        vals = bar.values()
        assert set(vals) == {"gla", "fc", "gip", "tip"}
        assert all(v == "Sum Assured" for v in vals.values())
        bar.set_values({"fc": "Flat Rand per Mem", "gla": "no-such-mode"})
        assert bar.values()["fc"] == "Flat Rand per Mem"
        assert bar.values()["gla"] == "Sum Assured"  # unknown -> first item

    def test_changed_signal(self, qapp):
        from gap_pricing_gui.category_split_bar import CategorySplitBar
        bar = CategorySplitBar()
        seen = []
        bar.changed.connect(seen.append)
        bar._combos["tip"].setCurrentIndex(1)
        assert seen and seen[-1]["tip"] == "Risk Profile"
        # set_values is signal-blocked
        bar.set_values({"gla": "Risk Profile"})
        assert len(seen) == 1
        assert bar.values()["gla"] == "Risk Profile"


# ---------------------------------------------------------------------------
# grid_view
# ---------------------------------------------------------------------------

class TestGridView:
    def test_qcolor_from_hex(self):
        from gap_pricing_gui.grid_view import _qcolor_from_hex
        assert _qcolor_from_hex(None) is None
        assert _qcolor_from_hex("") is None
        c = _qcolor_from_hex("#FF0000")
        assert c is not None and c.isValid()

    def test_populate_table_from_grid(self, qapp):
        from gap_pricing_gui.grid_view import populate_table_from_grid
        from reporting.grid_extract import GridSheet
        grid = GridSheet(title="t", n_rows=2, n_cols=2, rows=[
            [_cell("h1", bold=True, fill="#FF0000", font_color="FFFFFF",
                   align="center"),
             _cell("h2", align="right")],
            [_cell("a"), _cell("b")],
        ])
        table = QtWidgets.QTableWidget()
        populate_table_from_grid(table, grid)
        assert table.rowCount() == 2 and table.columnCount() == 2
        item = table.item(0, 0)
        assert item.text() == "h1" and item.font().bold()
        assert item.textAlignment() == int(QtCore.Qt.AlignCenter)
        assert table.item(0, 1).textAlignment() == int(
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    def test_trim_inactive_category_columns(self):
        from gap_pricing_gui.grid_view import _trim_inactive_category_columns
        rows = [[0, 1, 2, 3], [4, 5, 6, 7]]
        assert _trim_inactive_category_columns(rows, {1}) == [[0, 1, 2],
                                                             [4, 5, 6]]
        assert _trim_inactive_category_columns([], {1}) == []

    def test_resolve_pkey_for_section_label(self):
        from gap_pricing_gui.grid_view import _resolve_pkey_for_section_label
        assert _resolve_pkey_for_section_label("Life Assurance") == "gla"
        assert _resolve_pkey_for_section_label("Educate a Child") is None
        assert _resolve_pkey_for_section_label("bogus") is None

    def test_filter_cb_groups_to_active(self):
        from gap_pricing_gui.grid_view import filter_cb_groups_to_active
        groups = [
            ("Life Assurance", [[_cell(str(i)) for i in range(4)]]),
            ("Family Cover", [[_cell("fc1")]]),
            ("Extended Family Cover", [[_cell("x1")]]),
            ("Mystery Section", [[_cell("m1")]]),
        ]
        kept = filter_cb_groups_to_active(groups, {"gla", "ext_fc"}, {})
        assert [label for label, _ in kept] == [
            "Life Assurance", "Extended Family Cover"]
        # education fallback for unknown titles
        kept = filter_cb_groups_to_active(groups, {"educate_a_child"}, {})
        assert [label for label, _ in kept] == ["Mystery Section"]
        # category trimming
        kept = filter_cb_groups_to_active(
            [("Life Assurance", [[_cell("a"), _cell("b"), _cell("c"),
                                  _cell("d")]])],
            {"gla"}, {"gla": {2}})
        assert [c.display for c in kept[0][1][0]] == ["a", "b", "d"]

    def _sectioned_grid(self):
        from reporting.grid_extract import GridSheet
        fill = "#A6A6A6"
        rows = [
            [_cell("Life Assurance", bold=True, fill=fill), _cell(""), _cell("")],
            [_cell(""), _cell("a1"), _cell("a2")],
            [_cell("Family Cover", bold=True, fill=fill), _cell(""), _cell("")],
            [_cell(""), _cell("b1"), _cell("b2")],
        ]
        return GridSheet(title="t", n_rows=4, n_cols=3, rows=rows)

    def test_product_pill_grid_view(self, qapp):
        from gap_pricing_gui.grid_view import ProductPillGridView
        from reporting.grid_extract import GridSheet
        view = ProductPillGridView()
        view.set_grid(self._sectioned_grid())
        buttons = view.pill_group.buttons()
        assert len(buttons) == 2
        assert view.table.rowCount() == 2  # header + one data row
        assert view.table.item(1, 1).text() == "a1"
        buttons[1].click()
        assert view.table.item(1, 1).text() == "b1"
        # invalid section index is a no-op
        view._show_section(9)
        # grid without sections clears the table
        flat = GridSheet(title="f", n_rows=1, n_cols=1,
                         rows=[[_cell("only")]])
        view.set_grid(flat)
        assert view.table.rowCount() == 0
        assert len(view.pill_group.buttons()) == 0

    def test_pill_view_with_active_filter(self, qapp):
        from gap_pricing_gui.grid_view import ProductPillGridView
        view = ProductPillGridView()
        view.set_grid(self._sectioned_grid(),
                      active_products={"gla"},
                      active_categories={"gla": {1}})
        assert len(view.pill_group.buttons()) == 1


# ---------------------------------------------------------------------------
# po_view
# ---------------------------------------------------------------------------

class TestPoView:
    def test_product_active_and_visible(self):
        from gap_pricing_gui import po_view
        assert po_view._is_product_active("gla", {"gla"})
        assert not po_view._is_product_active("gla", {"fc"})
        assert po_view._is_product_active("education", {"educate_a_child"})
        assert po_view._is_product_active("education", {"education_guardian"})
        assert not po_view._is_product_active("education", {"gla"})
        visible = po_view.compute_visible_products(
            ["gla", "fc", "education"], {"gla", "education_guardian"})
        assert visible == ["gla", "education"]

    def test_label_and_content_helpers(self):
        from gap_pricing_gui import po_view
        row = [_cell(""), _cell("label"), _cell("v")]
        assert po_view._effective_label(row).display == "label"
        assert [c.display for c in po_view._effective_label_row(row)] == [
            "label", "v"]
        row2 = [_cell("merged"), _cell(""), _cell("v")]
        assert po_view._effective_label(row2).display == "merged"
        assert [c.display for c in po_view._effective_label_row(row2)] == [
            "merged", "v"]
        rows = [[_cell(""), _cell("")], [_cell("x"), _cell("y"), _cell("z")]]
        assert po_view._last_content_col(rows) == 2
        assert po_view._last_content_col([[_cell("")]]) == -1

    def test_priced_margin_editable(self):
        from gap_pricing_gui import po_view
        assert po_view.is_priced_margin_editable("gla", "profit_margin")
        assert not po_view.is_priced_margin_editable("gla", "pup_cost")
        assert po_view.is_priced_margin_editable("fc", "pup_cost")
        assert not po_view.is_priced_margin_editable("fc", "profit_margin")
        assert po_view.is_priced_margin_editable("education", "admin")
        assert po_view.is_priced_margin_editable("gla", "rate_basis")
        assert not po_view.is_priced_margin_editable("nope", "rate_basis")
        assert not po_view.is_priced_margin_editable("nope", "profit_margin")

    def test_find_express_row(self, qapp):
        from gap_pricing_gui import po_view
        table = QtWidgets.QTableWidget(3, 1)
        table.setItem(1, 0, QtWidgets.QTableWidgetItem(
            "Express over GLA Salary"))
        assert po_view.find_express_row(table) == 1
        table2 = QtWidgets.QTableWidget(1, 1)
        table2.setItem(0, 0, QtWidgets.QTableWidgetItem("other"))
        assert po_view.find_express_row(table2) is None

    def test_find_priced_margins_rows(self, qapp):
        from gap_pricing_gui import po_view
        table = QtWidgets.QTableWidget(20, 2)
        table.setItem(4, 0, QtWidgets.QTableWidgetItem(
            "Priced Margins - % of Net"))
        rows = po_view.find_priced_margins_rows(table)
        assert rows is not None
        assert rows["conversion_option"] == 6
        assert rows["rate_basis"] == 6 + 11
        empty = QtWidgets.QTableWidget(2, 1)
        assert po_view.find_priced_margins_rows(empty) is None
        short = QtWidgets.QTableWidget(8, 1)
        short.setItem(1, 0, QtWidgets.QTableWidgetItem(
            "Priced Margins - % of Net"))
        assert po_view.find_priced_margins_rows(short) is None

    def _po_grid(self):
        from reporting.grid_extract import GridSheet
        fill = "#A6A6A6"

        def hdr(txt):
            return [_cell(txt, bold=True, fill=fill), _cell(""), _cell(""),
                    _cell("")]

        def brow(lbl, g, f):
            return [_cell(""), _cell(lbl), _cell(g), _cell(f)]

        def prow(lbl, v):
            return [_cell(""), _cell(lbl), _cell(v), _cell("")]

        rows = [
            hdr("Scheme Details"),
            brow("Members", "100", "50"),
            brow("Premium", "10", "5"),
            hdr("GLA Section"),
            prow("GLA rate", "g1"),
            prow("GLA total", "g2"),
            hdr("FC Section"),
            prow("FC rate", "f1"),
        ]
        return GridSheet(title="po", n_rows=len(rows), n_cols=4, rows=rows)

    def test_filter_po_grid_to_active(self):
        from gap_pricing_gui.po_view import filter_po_grid_to_active
        from reporting.grid_extract import GridSheet
        grid = self._po_grid()
        out = filter_po_grid_to_active(grid, ["gla", "fc"], ["gla"])
        assert out is not grid
        displays = {c.display for row in out.rows for c in row}
        assert "GLA Section" in displays
        assert "FC Section" not in displays and "f1" not in displays
        assert all(len(row) == out.n_cols for row in out.rows)
        # early returns
        assert filter_po_grid_to_active(grid, [], ["gla"]) is grid
        flat = GridSheet(title="f", n_rows=1, n_cols=1, rows=[[_cell("x")]])
        assert filter_po_grid_to_active(flat, ["gla"], ["gla"]) is flat
        assert filter_po_grid_to_active(
            grid, ["gla", "fc", "tip"], ["gla"]) is grid  # count mismatch


# ---------------------------------------------------------------------------
# single_quote_view
# ---------------------------------------------------------------------------

def _product_frames():
    gla = pl.DataFrame({
        "product_active": [True, True],
        "total_annual_premium": [100.0, 200.0],
        "profit_margin_amount": [10.0, 20.0],
        "total_cover": [1000.0, 2000.0],
        "member_id": [1, 2],
    })
    fc = pl.DataFrame({
        "total_annual_premium": [50.0],
        "profit_margin_amount": [5.0],
        "member_id": [9],
    })
    return {"gla": gla, "fc": fc}


def _fake_res():
    scheme = SimpleNamespace(scheme_name="Scheme A", quote_number="Q1",
                             quote_type="New Business", category_split={})
    return SimpleNamespace(
        ctr_bundle=SimpleNamespace(scheme=scheme),
        assump_bundle=SimpleNamespace(tables={}, globals={}),
        scheme_name="Scheme A",
        quote_number="Q1",
        active_products=["gla", "fc"],
        per_product_results=_product_frames(),
        aggregates={"total_annual_premium": 350.0,
                    "total_gross_risk_premium": 376.25,
                    "total_cover": 9000.0},
    )


class TestSingleQuoteView:
    def test_product_display_name(self):
        from gap_pricing_gui.single_quote_view import product_display_name
        assert product_display_name("gla") == "GLA"
        assert product_display_name("weird_key") == "Weird Key"

    def test_fmt(self):
        from gap_pricing_gui.single_quote_view import _fmt
        assert _fmt("Total Annual Premium", 1234.5) == "R 1,234.50"
        assert _fmt("% of Scheme Premium", 50.5) == "50.50%"
        assert _fmt("Members", 1234) == "1,234"
        assert _fmt("Product Name", "abc") == "abc"

    def test_build_product_summary_df(self):
        from gap_pricing_gui.single_quote_view import build_product_summary_df
        df = build_product_summary_df(_fake_res())
        assert df.height == 2
        row = df.row(0, named=True)
        assert row["Product Name"] == "GLA"
        assert row["Members"] == 2 and row["Covered"] == 2
        assert row["Total Annual Premium"] == 300.0
        assert row["Profit Margin"] == 30.0
        assert df["% of Scheme Premium"].sum() == pytest.approx(100.0)

    def test_populate_summary_table(self, qapp):
        from gap_pricing_gui.single_quote_view import (
            build_product_summary_df,
            populate_summary_table,
        )
        table = QtWidgets.QTableWidget()
        df = build_product_summary_df(_fake_res())
        populate_summary_table(table, df)
        assert table.rowCount() == df.height + 1
        # "Product" col is hidden; first display column is "Product Name"
        assert table.item(0, 0).text() == "GLA"
        assert table.item(df.height, 0).text() == "TOTAL"
        assert table.item(df.height, 0).font().bold()

    def test_view_lifecycle(self, qapp, monkeypatch):
        from gap_pricing_gui.single_quote_view import SingleQuoteView
        view = SingleQuoteView()
        assert "Price a quote" in view.status.text()
        res = _fake_res()
        view.load_result(res)
        assert view.product_combo.count() == 2
        assert view.parquet_product_combo.count() == 3
        assert view.table.rowCount() == 3  # 2 products + total row
        assert view.parquet_btn.isVisibleTo(view)
        # reload (exercises recursive layout clear)
        view.load_result(_fake_res())
        assert view._view_mode == "summary"

        # double-click row 0 -> member detail for gla
        ix = view.table.model().index(0, 0)
        view.on_dblclick(ix)
        assert view._view_mode == "gla"
        assert view.back_btn.isVisibleTo(view)
        assert view.table.rowCount() == 2
        # combo switch to fc
        view._on_combo_changed(1)
        assert view._view_mode == "fc"
        assert view.table.rowCount() == 1
        # double-click in product view is ignored
        view.on_dblclick(view.table.model().index(0, 0))
        assert view._view_mode == "fc"
        view.on_back_to_summary()
        assert view._view_mode == "summary"

        # parquet view
        monkeypatch.setattr(
            "reporting.meta_engine.build_engine_frame",
            lambda d: pl.DataFrame({"product": ["gla", "fc"], "v": [1, 2]}))
        view.on_show_parquet()
        assert view._view_mode == "parquet"
        assert view.parquet_controls.isVisibleTo(view)
        assert view.parquet_model.rowCount() == 2
        view.parquet_product_combo.setCurrentIndex(1)  # gla
        assert view.parquet_model.rowCount() == 1
        view.parquet_filter.setText("zzz-no-match")
        assert view.parquet_model.rowCount() == 0

        view.reset()
        assert view.result is None
        assert view.table.rowCount() == 0
        assert "Price a quote" in view.status.text()

    def test_parquet_without_result(self, qapp):
        from gap_pricing_gui.single_quote_view import SingleQuoteView
        view = SingleQuoteView()
        view.on_show_parquet()  # no result -> no-op
        assert view._view_mode == "summary"

    def test_dblclick_out_of_range(self, qapp):
        from gap_pricing_gui.single_quote_view import SingleQuoteView
        view = SingleQuoteView()
        view.load_result(_fake_res())
        # row beyond product list -> ignored
        view.on_dblclick(view.table.model().index(2, 0))
        assert view._view_mode == "summary"


# ---------------------------------------------------------------------------
# batch_detail_dialog
# ---------------------------------------------------------------------------

class TestBatchDetailDialog:
    def test_build_batch_product_summary_df(self):
        from gap_pricing_gui.batch_detail_dialog import build_batch_product_summary_df
        frames = _product_frames()
        df = build_batch_product_summary_df(["gla", "fc"], frames)
        assert df.height == 2
        assert set(df.columns) == {
            "Product", "Product Name", "Members", "Covered",
            "Total Annual Premium", "Avg Premium (Covered)", "Profit Margin",
            "% of Scheme Premium"}
        row = df.row(0, named=True)
        assert row["Product Name"] == "GLA"
        assert row["Members"] == 2 and row["Covered"] == 2
        assert row["Avg Premium (Covered)"] == 150.0
        assert df["% of Scheme Premium"].sum() == pytest.approx(100.0)

    def _make_dialog(self, tmp_path):
        from gap_pricing_gui.batch_detail_dialog import BatchDetailDialog
        frames = _product_frames()
        ctr = SimpleNamespace(
            scheme=SimpleNamespace(scheme_name="Scheme A", quote_number="Q9"),
            active_products=["gla"],
            active_categories={"gla": {1}},
        )
        return BatchDetailDialog(ctr, object(), {"gla": frames["gla"]},
                                 "path/x.ctr", out_dir=str(tmp_path))

    def test_dialog_construction(self, qapp, tmp_path):
        dlg = self._make_dialog(tmp_path)
        try:
            assert "Scheme A" in dlg.windowTitle()
            assert "Q9" in dlg.windowTitle()
            assert dlg.summary_table.rowCount() == 2  # product + total
            assert dlg.stack.count() == 3
        finally:
            dlg.close()

    def test_dialog_lazy_tabs(self, qapp, tmp_path, monkeypatch):
        import reporting.category_breakdown as rcb
        import reporting.inputs as ri
        import reporting.pricing_outputs as rpo
        monkeypatch.setattr(ri, "gather_cb_data", lambda *a, **k: {"cb": 1})
        monkeypatch.setattr(ri, "gather_po_data",
                            lambda *a, **k: {"products": []})
        monkeypatch.setattr(
            rcb, "write_category_breakdown",
            lambda wb, d: wb.create_sheet("Category Breakdown")
            .__setitem__("A1", "cb"))
        monkeypatch.setattr(
            rpo, "write_pricing_outputs",
            lambda wb, d: wb.create_sheet("Pricing Outputs")
            .__setitem__("A1", "po"))
        dlg = self._make_dialog(tmp_path)
        try:
            dlg._show_tab(1)
            assert dlg._cb_grid is not None
            cb_first = dlg._cb_grid
            dlg._show_tab(1)
            assert dlg._cb_grid is cb_first  # cached
            dlg._show_tab(2)
            assert dlg._po_grid is not None
            dlg._show_tab(0)
        finally:
            dlg.close()

    def test_dialog_save_html(self, qapp, tmp_path, monkeypatch):
        saved = []
        monkeypatch.setattr("reporting.write_pricing_report",
                            lambda **kw: saved.append(kw))
        monkeypatch.setattr("gap_pricing_gui.file_access.output_directory",
                            lambda loc=None: tmp_path)
        infos = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                            lambda *a: infos.append(a))
        dlg = self._make_dialog(tmp_path)
        try:
            dlg.on_save_html()
            assert saved and saved[0]["write_html"] is True
            assert saved[0]["write_xlsx"] is False
            assert dlg.status.text().startswith("HTML report saved:")
            assert infos
        finally:
            dlg.close()

    def test_dialog_save_html_failure(self, qapp, tmp_path, monkeypatch):
        def boom(**kw):
            raise RuntimeError("html boom")

        monkeypatch.setattr("reporting.write_pricing_report", boom)
        monkeypatch.setattr("gap_pricing_gui.file_access.output_directory",
                            lambda loc=None: tmp_path)
        crits = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                            lambda *a: crits.append(a))
        dlg = self._make_dialog(tmp_path)
        try:
            dlg.on_save_html()
            assert "HTML save failed" in dlg.status.text()
            assert crits and "html boom" in str(crits[0][-1])
        finally:
            dlg.close()


# ---------------------------------------------------------------------------
# main_window
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _Scheme:
    scheme_name: str = "Scheme A"
    quote_number: str = "Q1"
    quote_type: str = "New"
    category_split: dict = dataclasses.field(default_factory=dict)
    express_over_gla: dict = dataclasses.field(default_factory=dict)
    priced_margin_overrides: dict = dataclasses.field(default_factory=dict)
    rate_basis_by_product: dict = dataclasses.field(default_factory=dict)
    education_product: object = None


@dataclasses.dataclass
class _Ctr:
    scheme: _Scheme = dataclasses.field(default_factory=_Scheme)
    active_products: tuple = ("gla",)
    active_categories: dict = dataclasses.field(
        default_factory=lambda: {"gla": {1}})
    warnings: tuple = ()


def _window_res():
    df = pl.DataFrame({
        "product_active": [True, True],
        "total_annual_premium": [100.0, 200.0],
        "profit_margin_amount": [10.0, 20.0],
        "total_cover": [1000.0, 2000.0],
        "member_id": [1, 2],
    })
    return SimpleNamespace(
        ctr_bundle=_Ctr(),
        assump_bundle=SimpleNamespace(tables={}, globals={}),
        scheme_name="Scheme A",
        quote_number="Q1",
        active_products=["gla"],
        per_product_results={"gla": df},
        aggregates={"total_annual_premium": 300.0,
                    "total_gross_risk_premium": 322.5,
                    "total_cover": 3000.0},
    )


class _FakeStartup(QtCore.QThread):
    done = QtCore.Signal(object, object)
    failed = QtCore.Signal(str)

    def run(self):
        self.done.emit(object(), object())


class _FakePrewarm(QtCore.QThread):
    done = QtCore.Signal(object, object, object)
    failed = QtCore.Signal(str)

    def __init__(self, *args):
        super().__init__()
        self.args = args

    def run(self):
        pass  # tests drive _on_view_prewarm_done directly


class _FakeBatch(QtCore.QThread):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, paths, basis, assump, out_dir, max_workers,
                 write_report=False):
        super().__init__()
        self.paths = paths
        self.started = False
        self.results_cache = {"x/a.ctr": ("ctr", "ass", {"gla": "df"})}

    def start(self):
        self.started = True

    def run(self):
        pass


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    from gap_pricing_gui import main_window as mw
    from gap_pricing_gui import warm_state
    monkeypatch.setattr(mw, "validate_gui_settings", lambda: settings)
    monkeypatch.setattr(mw, "StartupThread", _FakeStartup)
    monkeypatch.setattr(mw, "ViewPrewarmThread", _FakePrewarm)
    warm_state.UI_WARM_DONE.set()
    warm_state.ENGINE_WARM_DONE.set()
    win = mw.GapWindow()
    _wait_until(lambda: win._startup_data_ready)
    _wait_until(lambda: win.priceb.isEnabled())
    yield win
    for t in getattr(win, "_view_prewarm_threads", []):
        t.wait(5000)
    for t in (getattr(win, "_startup", None),
              getattr(win, "_batch_thread", None)):
        if isinstance(t, QtCore.QThread):
            t.wait(5000)
    win.close()
    qapp.processEvents()


def _patch_mw_reporting(monkeypatch):
    import reporting.category_breakdown as rcb
    import reporting.inputs as ri
    import reporting.pricing_outputs as rpo
    monkeypatch.setattr(ri, "gather_cb_data", lambda *a, **k: {"cb": 1})
    monkeypatch.setattr(ri, "gather_po_data",
                        lambda *a, **k: {"products": ["gla"]})

    def fake_cb(wb, data):
        ws = wb.create_sheet("Category Breakdown")
        ws["A1"] = "CB"
        ws["A2"] = "value"

    def fake_po(wb, data):
        ws = wb.create_sheet("Pricing Outputs")
        ws["A1"] = "Pricing Outputs"
        ws["A2"] = "Express over GLA Salary"
        ws["B2"] = "No"
        ws["A4"] = "Priced Margins - % of Net"
        for i in range(12):
            ws.cell(row=6 + i, column=1, value=f"m{i}")
            ws.cell(row=6 + i, column=2, value="0.1")

    monkeypatch.setattr(rcb, "write_category_breakdown", fake_cb)
    monkeypatch.setattr(rpo, "write_pricing_outputs", fake_po)


class TestGapWindow:
    def test_constructs_and_ready(self, window):
        assert window.windowTitle() == "GAP Pricing Engine"
        assert window.nav.count() == 4
        assert window.nav.currentRow() == 0
        assert window.basis is not None and window.assump is not None
        assert not window.saveb.isEnabled()
        assert not window.save_html_btn.isEnabled()
        assert not window.load_db_btn.isEnabled()  # db disabled in settings
        assert window.view_stack.count() == 4
        assert window.workers.value() >= 1

    def test_config_error_window(self, qapp, monkeypatch):
        from gap_pricing_gui import main_window as mw
        monkeypatch.setattr(
            mw, "validate_gui_settings",
            lambda: (_ for _ in ()).throw(ConfigurationError("no mount")))
        win = mw.GapWindow()
        assert win.settings is None
        assert "no mount" in win.centralWidget().text()
        assert "configuration error" in win.windowTitle()
        win.close()

    def test_nav_and_status(self, window):
        window.nav.setCurrentRow(1)
        assert not window.filter_row_host.isHidden()
        window.nav.setCurrentRow(2)
        assert window.filter_row_host.isHidden()
        assert "Price a quote first" in window.status.currentMessage()
        window.nav.setCurrentRow(3)
        assert "Price a quote first" in window.status.currentMessage()
        window.nav.setCurrentRow(0)
        assert window.view_stack.currentIndex() == 0

    def test_startup_fail(self, window, monkeypatch):
        crits = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                            lambda *a: crits.append(a))
        window.on_startup_fail("tb-boom")
        assert crits and "tb-boom" in str(crits[0][-1])
        assert window.browse.isEnabled()  # busy cleared

    def test_price_requires_path(self, window, monkeypatch):
        warns = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                            lambda *a: warns.append(a))
        window.path.setText("")
        window.on_price()
        assert warns and "CTR" in str(warns[0][-1])
        assert window.result is None

    def test_price_success_and_save(self, window, monkeypatch):
        res = _window_res()
        monkeypatch.setattr("gap_pricing_gui.quote_runner.price_quote",
                            lambda p, progress_cb=None: res)
        window.path.setText("x.ctr")
        window.on_price()
        assert window.result is res
        assert window.saveb.isEnabled()
        assert window.save_html_btn.isEnabled()

        calls = []
        monkeypatch.setattr(
            "gap_pricing_gui.quote_runner.save_report",
            lambda r, **kw: calls.append(kw) or Path("report.xlsx"))
        infos = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                            lambda *a: infos.append(a))
        window.on_save()
        assert calls == [{}]
        window.on_save_html()
        assert calls[-1]["write_html"] is True
        assert calls[-1]["write_xlsx"] is False

    def test_price_from_db(self, window, monkeypatch):
        res = _window_res()
        monkeypatch.setattr(
            "gap_pricing_gui.quote_runner.price_quote_from_db",
            lambda rid, progress_cb=None: res)
        window._db_run_id = "run-42"
        window.path.setText("db label")
        window.on_price()
        assert window.result is res
        window._db_run_id = None

    def test_price_failure(self, window, monkeypatch):
        def boom(p, progress_cb=None):
            raise RuntimeError("price boom")

        monkeypatch.setattr("gap_pricing_gui.quote_runner.price_quote", boom)
        crits = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                            lambda *a: crits.append(a))
        window.path.setText("x.ctr")
        window.on_price()
        assert crits and "price boom" in str(crits[0][-1])

    def test_save_failure(self, window, monkeypatch):
        window.result = _window_res()

        def boom(r, **kw):
            raise RuntimeError("save boom")

        monkeypatch.setattr("gap_pricing_gui.quote_runner.save_report", boom)
        crits = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                            lambda *a: crits.append(a))
        window.on_save()
        assert crits
        window.on_save_html()
        assert "HTML save failed" in window.status.currentMessage()

    def test_browse_and_pickers(self, window, tmp_path, monkeypatch):
        from gap_pricing_gui import main_window as mw
        target = tmp_path / "f.ctr"
        target.write_bytes(b"x")
        monkeypatch.setattr(mw, "input_directory", lambda loc=None: tmp_path)
        monkeypatch.setattr(mw, "input_file", lambda loc: Path(loc))
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                            lambda *a, **k: (str(target), ""))
        window.on_browse()
        assert window.path.text() == str(target)
        assert window._db_run_id is None
        # cancel keeps path
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                            lambda *a, **k: ("", ""))
        window.path.setText("keep")
        window.on_browse()
        assert window.path.text() == "keep"
        # invalid input -> warning
        monkeypatch.setattr(
            mw, "input_directory",
            lambda loc=None: (_ for _ in ()).throw(ConfigurationError("bad")))
        warns = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                            lambda *a: warns.append(a))
        window.on_browse()
        assert warns
        # folder / output pickers
        monkeypatch.setattr(mw, "input_directory", lambda loc=None: tmp_path)
        monkeypatch.setattr(mw, "output_directory",
                            lambda loc=None: tmp_path / "out")
        monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                            lambda *a, **k: str(tmp_path))
        window.pick_folder()
        assert window.bfolder.text() == str(tmp_path)
        window.pick_output()
        assert window.boutput.text() == str(tmp_path / "out")

    def test_batch_flow(self, window, tmp_path, monkeypatch):
        from gap_pricing_gui import main_window as mw
        monkeypatch.setattr(mw, "input_directory", lambda loc=None: tmp_path)
        monkeypatch.setattr(mw, "batch_ctr_files",
                            lambda f: [tmp_path / "a.ctr"])
        monkeypatch.setattr(mw, "output_directory",
                            lambda loc=None: tmp_path / "out")
        monkeypatch.setattr(mw, "BatchThread", _FakeBatch)
        window.on_batch()
        assert isinstance(window._batch_thread, _FakeBatch)
        assert window._batch_thread.started
        df = pl.DataFrame({"ctr_name": ["a.ctr"], "status": ["OK"]})
        window.on_batch_done(df)
        assert window.batch_model.rowCount() == 1
        assert "batch done" in window.status.currentMessage()
        # failure path
        crits = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                            lambda *a: crits.append(a))
        window.on_batch_fail("tb")
        assert crits and "BATCH ERROR" in window.status.currentMessage()

    def test_batch_blocked_and_invalid(self, window, monkeypatch):
        warns = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                            lambda *a: warns.append(a))
        window.basis = None
        window.on_batch()
        assert warns and "Snapshots still loading" in str(warns[0][-1])
        window.basis = object()
        from gap_pricing_gui import main_window as mw
        monkeypatch.setattr(
            mw, "input_directory",
            lambda loc=None: (_ for _ in ()).throw(ConfigurationError("s3 no")))
        warns.clear()
        window.on_batch()
        assert warns
        assert window._batch_thread is None

    def test_batch_dblclick(self, window, tmp_path, monkeypatch):
        from gap_pricing_gui import main_window as mw
        window.batch_model.set_df(pl.DataFrame(
            {"ctr_name": ["a.ctr", "b.ctr"], "status": ["OK", "OK"]}))
        window._batch_cache = {str(tmp_path / "a.ctr"): ("ctr", "ass", "res")}
        opened = []
        monkeypatch.setattr(
            mw, "BatchDetailDialog",
            lambda *a, **k: opened.append((a, k))
            or SimpleNamespace(exec=lambda: None))
        window.on_batch_dblclick(window.batch_model.index(0, 0))
        assert opened and opened[0][0][2] == "res"
        assert opened[0][0][3] == str(tmp_path / "a.ctr")
        window.on_batch_dblclick(window.batch_model.index(1, 0))
        assert "No cached pricing data" in window.status.currentMessage()
        # index without ctr_name column -> early return
        window.batch_model.set_df(pl.DataFrame({"x": [1]}))
        window.on_batch_dblclick(window.batch_model.index(0, 0))

    def test_apply_filter(self, window):
        window.batch_model.set_df(pl.DataFrame(
            {"ctr_name": ["alpha", "beta"], "v": [1, 2]}))
        window.nav.setCurrentRow(1)
        window.filt.setText("alpha")
        window._apply_filter()
        assert window.batch_model.rowCount() == 1
        window.filt.setText("")
        window._apply_filter()
        assert window.batch_model.rowCount() == 2
        window.nav.setCurrentRow(0)

    def test_load_from_db_dialog(self, window, monkeypatch):
        df = pl.DataFrame({
            "run_id": ["r1", "r2"],
            "scheme_name": ["Alpha", "Beta"],
            "scheme_code": ["C1", "C2"],
            "quote_number": ["Q1", "Q2"],
            "broker_name": ["B1", "B2"],
            "doer": ["D", "D"],
            "checker": ["Ch", "Ch"],
            "commencement_date": ["2025-01-01", "2025-02-02"],
            "started_at": ["t1", "t2"],
            "rerun_number": [1, 1],
        })
        monkeypatch.setattr("gap_pricing_gui.quote_runner.list_db_schemes",
                            lambda status_filter="success": df)

        def accept_and_select(dlg):
            tbl = dlg.findChildren(QtWidgets.QTableWidget)[0]
            tbl.selectRow(0)
            return QtWidgets.QDialog.Accepted

        monkeypatch.setattr(QtWidgets.QDialog, "exec", accept_and_select)
        window.on_load_from_db()
        assert window._db_run_id == "r1"
        assert "Alpha" in window.path.text()

        monkeypatch.setattr(QtWidgets.QDialog, "exec",
                            lambda dlg: QtWidgets.QDialog.Rejected)
        window._db_run_id = "prev"
        window.path.setText("keep")
        window.on_load_from_db()
        assert window._db_run_id == "prev"
        assert window.path.text() == "keep"

    def test_load_from_db_errors(self, window, monkeypatch):
        crits, infos, warns = [], [], []
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                            lambda *a: crits.append(a))
        monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                            lambda *a: infos.append(a))
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                            lambda *a: warns.append(a))
        monkeypatch.setattr(
            "gap_pricing_gui.quote_runner.list_db_schemes",
            lambda status_filter="success": (_ for _ in ())
            .throw(RuntimeError("db down")))
        window.on_load_from_db()
        assert crits and "db down" in str(crits[0][-1])
        monkeypatch.setattr(
            "gap_pricing_gui.quote_runner.list_db_schemes",
            lambda status_filter="success": pl.DataFrame(
                schema={"run_id": pl.Utf8}))
        window.on_load_from_db()
        assert infos and "No schemes" in str(infos[0][-2])

    def test_show_builds_views(self, window, monkeypatch, qapp):
        _patch_mw_reporting(monkeypatch)
        window._show(_window_res())
        qapp.processEvents()
        assert window.result is not None
        window.nav.setCurrentRow(2)
        assert window._cb_grid is not None
        window.nav.setCurrentRow(3)
        assert window._po_grid is not None
        assert window._po_products_visible == ["gla"]
        assert window.po_table.rowCount() > 0
        # repeat build uses cached grids
        window._build_cb_view()
        window._build_po_view()

    def test_view_prewarm_done(self, window, qapp):
        from reporting.grid_extract import GridSheet
        window._show(_window_res())
        grid = GridSheet(title="t", n_rows=0, n_cols=0, rows=[])
        # seed the cached grids first so nav switching uses them
        window._on_view_prewarm_done(grid, grid, [])
        assert window._cb_grid is grid and window._po_grid is grid
        assert window._po_products_visible == []
        window.nav.setCurrentRow(2)
        assert window._cb_grid is grid
        window.nav.setCurrentRow(3)
        assert window._po_grid is grid

    def test_split_changed(self, window, monkeypatch, qapp):
        _patch_mw_reporting(monkeypatch)
        window._show(_window_res())
        qapp.processEvents()
        window._on_split_changed({"gla": "Risk Profile"})
        assert window._cb_grid is not None
        # no result -> early return
        window.result = None
        window._on_split_changed({"gla": "x"})  # must not raise

    def _build_po(self, window, monkeypatch, qapp):
        _patch_mw_reporting(monkeypatch)
        window._show(_window_res())
        window.nav.setCurrentRow(3)
        qapp.processEvents()
        assert window._po_products_visible == ["gla"]

    def test_express_combo_override(self, window, monkeypatch, qapp):
        from gap_pricing_gui.po_view import find_express_row
        self._build_po(window, monkeypatch, qapp)
        row = find_express_row(window.po_table)
        assert row is not None
        combo = window.po_table.cellWidget(row, 1)
        assert isinstance(combo, QtWidgets.QComboBox)
        combo.setCurrentText("Yes")
        qapp.processEvents()
        assert window._po_express_override == {"gla": True}

    def test_priced_margin_edit(self, window, monkeypatch, qapp):
        from gap_pricing_gui.po_view import find_priced_margins_rows
        self._build_po(window, monkeypatch, qapp)
        row_map = find_priced_margins_rows(window.po_table)
        assert row_map is not None
        edit = window.po_table.cellWidget(row_map["profit_margin"], 1)
        assert isinstance(edit, QtWidgets.QLineEdit)
        edit.setText("12.5")
        edit.editingFinished.emit()
        qapp.processEvents()
        assert (window._po_margin_override["gla"]["profit_margin"]
                == pytest.approx(0.125))
        # blank -> override removed
        edit = window.po_table.cellWidget(row_map["profit_margin"], 1)
        edit.setText("")
        edit.editingFinished.emit()
        assert window._po_margin_override["gla"] == {}
        # invalid -> warning
        warns = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                            lambda *a: warns.append(a))
        edit = window.po_table.cellWidget(row_map["profit_margin"], 1)
        edit.setText("nope%")
        edit.editingFinished.emit()
        assert warns

    def test_rate_basis_edit(self, window, monkeypatch, qapp):
        from gap_pricing_gui.po_view import find_priced_margins_rows
        self._build_po(window, monkeypatch, qapp)
        row_map = find_priced_margins_rows(window.po_table)
        edit = window.po_table.cellWidget(row_map["rate_basis"], 1)
        assert isinstance(edit, QtWidgets.QLineEdit)
        edit.setText("150")
        edit.editingFinished.emit()
        qapp.processEvents()
        assert window._po_rate_basis_override["gla"] == pytest.approx(1.5)
        edit = window.po_table.cellWidget(row_map["rate_basis"], 1)
        edit.setText("")
        edit.editingFinished.emit()
        assert window._po_rate_basis_override["gla"] == pytest.approx(1.0)

    def test_close_event_paths(self, window, monkeypatch):
        class Ev:
            def __init__(self):
                self.accepted = self.ignored = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                self.ignored = True

        ev = Ev()
        window.closeEvent(ev)
        assert ev.accepted
        # pending background writes -> deferred then accepted at max wait
        from gap_pricing_gui import warm_state
        monkeypatch.setattr(warm_state, "db_writes_pending", lambda: 1)
        monkeypatch.setattr(QtCore.QTimer, "singleShot",
                            lambda *a, **k: None)
        ev2 = Ev()
        window.closeEvent(ev2)
        assert ev2.ignored
        window._close_grace_elapsed_ms = window._CLOSE_GRACE_MAX_WAIT_MS
        ev3 = Ev()
        window.closeEvent(ev3)
        assert ev3.accepted

    def test_close_waits_for_running_thread(self, window, qapp, monkeypatch):
        class Ev:
            def __init__(self):
                self.accepted = self.ignored = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                self.ignored = True

        class Sleeper(QtCore.QThread):
            def run(self):
                self.msleep(300)

        t = Sleeper()
        t.start()
        deadline = time.monotonic() + 3
        while not t.isRunning() and time.monotonic() < deadline:
            time.sleep(0.01)
        window._batch_thread = t
        monkeypatch.setattr(QtCore.QTimer, "singleShot",
                            lambda *a, **k: None)
        ev = Ev()
        window.closeEvent(ev)
        assert ev.ignored
        assert t.wait(5000)

    def test_run_function(self, qapp, monkeypatch):
        from gap_pricing_gui import main_window as mw
        shown = []

        class FakeWin:
            def show(self):
                shown.append(True)

            def close(self):
                pass

        monkeypatch.setattr(mw, "GapWindow", lambda: FakeWin())
        assert mw.run() == 0
        assert shown


# ---------------------------------------------------------------------------
# launch_gap_gui.main
# ---------------------------------------------------------------------------

def test_launch_main_with_existing_app(qapp, tmp_path, monkeypatch):
    from gap_pricing_gui import launch_gap_gui as lg
    from gap_pricing_gui import warm_state
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(lg, "validate_gui_settings", lambda: settings)
    made = []

    class FakeWin:
        def __init__(self):
            made.append(self)

        def show(self):
            pass

        def raise_(self):
            pass

        def activateWindow(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("gap_pricing_gui.main_window.GapWindow", FakeWin)
    assert lg.main() == 0
    assert made and qapp._gap_main_window is made[0]
    # background warm threads set the warm-state events
    deadline = time.monotonic() + 10
    while not (warm_state.UI_WARM_DONE.is_set()
               and warm_state.ENGINE_WARM_DONE.is_set()) \
            and time.monotonic() < deadline:
        time.sleep(0.05)
    warm_state.UI_WARM_DONE.clear()
    warm_state.ENGINE_WARM_DONE.clear()
