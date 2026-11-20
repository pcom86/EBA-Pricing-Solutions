"""Coverage tests for reporting constants, lazy __init__, grid helpers and meta engine."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import polars as pl
import pytest
import reporting
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from reporting import constants
from reporting.grid_extract import GridCell, GridSheet, _format_value, sheet_to_grid
from reporting.grid_sections import group_grid_by_sections
from reporting.meta_engine import (
    ENGINE_VERSION,
    _fmt_date,
    _guess_format,
    build_engine_frame,
    build_meta_rows,
    write_calc_companion_parquet,
    write_engine_sheet,
    write_meta_sheet,
)

# --------------------------------------------------------------------------
# reporting/__init__.py lazy attribute loading
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_lazy_attrs_resolve_and_cache():
    assert "gather_cb_data" in dir(reporting)
    fn = reporting.gather_cb_data
    assert callable(fn)
    # second access should hit the cached globals entry
    assert reporting.gather_cb_data is fn
    assert reporting.write_pricing_report is not None
    assert reporting.write_category_breakdown is not None
    assert reporting.write_pricing_outputs is not None
    assert reporting.gather_po_data is not None
    with pytest.raises(AttributeError):
        reporting.does_not_exist  # noqa: B018


# --------------------------------------------------------------------------
# constants.py
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_constants_consistency():
    assert set(constants.CB_PRODUCTS).issubset(set(constants.CB_RENDER_ORDER))
    assert set(constants.CB_RENDER_ORDER) == set(constants.CB_SECTION_TITLE)
    assert set(constants.CB_RENDER_ORDER) == set(constants.CB_LABEL_SUFFIX)
    assert set(constants.CB_RENDER_ORDER) == set(constants.CB_ADDONS)
    assert constants.N_CATEGORIES == 20
    assert constants.PO_BASIS_TITLE["gla"] == "GLA - 100% Basis"
    assert set(constants.PO_PRODUCT_DISPLAY) == set(constants.PO_TECH_TITLE)


# --------------------------------------------------------------------------
# grid_extract._format_value
# --------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(
    "value,fmt,expected",
    [
        (None, "#,##0", ""),
        ("abc", "#,##0", "abc"),
        (0.0, 'R #,##0;R -#,##0;"-"', "-"),
        (1234.5, "R #,##0.00", "R 1,234.50"),
        (1234.6, "R #,##0", "R 1,235"),
        (1234.5, "#,##0.00", "1,234.50"),
        (1234.6, "#,##0", "1,235"),
        (0.12345678, "0.000000%", "12.345678%"),
        (0.12345, "0.0000%", "12.3450%"),
        (0.1234, "0.000%", "12.340%"),
        (0.5, "0.00%", "50.00%"),
        (0.5, "0%", "50%"),
        (0.123456789, "0.00000000", "0.12345679"),
        (1.5, "0.000;-0.000", "1.500"),
        (1.5, "0.0000;-0.0000", "1.5000"),
        (1.5, "0.00;-0.00", "1.50"),
        (7.0, "0.000", "7.000"),
        (7.0, "0.0000", "7.0000"),
        (7.0, "0.00", "7.00"),
        ("x", "yyyy-mm-dd", "x"),
        (1.25, "unknown-format", "1.25"),
    ],
)
def test_format_value(value, fmt, expected):
    assert _format_value(value, fmt) == expected


# --------------------------------------------------------------------------
# grid_extract.sheet_to_grid
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_sheet_to_grid_extracts_styles_and_skips_hidden_rows():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "T"

    c = ws.cell(row=1, column=1, value="Section")
    c.font = Font(bold=True, color="FFFFFFFF")
    c.fill = PatternFill("solid", fgColor="A6A6A6")
    c.alignment = Alignment(horizontal="center")
    c.border = Border(left=Side(style="thin"))

    ws.cell(row=2, column=1, value=1234.5).number_format = "R #,##0.00"
    ws.cell(row=3, column=1, value=0.5).number_format = "0.00%"

    ws.row_dimensions[4].hidden = True
    ws.cell(row=4, column=1, value="secret")

    grid = sheet_to_grid(ws, title="MyTitle")

    assert grid.title == "MyTitle"
    assert grid.n_rows == 3  # hidden row skipped
    assert grid.n_cols == 1

    head = grid.rows[0][0]
    assert head.value == "Section"
    assert head.bold is True
    assert head.fill == "#A6A6A6"
    assert head.font_color == "FFFFFF"
    assert head.align == "center"
    assert head.bordered is True

    assert grid.rows[1][0].display == "R 1,234.50"
    assert grid.rows[2][0].display == "50.00%"


@pytest.mark.unit
def test_sheet_to_grid_defaults_title_to_sheet_name():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Auto"
    ws.cell(row=1, column=1, value="x")
    grid = sheet_to_grid(ws)
    assert grid.title == "Auto"


# --------------------------------------------------------------------------
# grid_sections.group_grid_by_sections
# --------------------------------------------------------------------------

def _cell(display, bold=False, fill=None):
    return GridCell(value=display, display=display, bold=bold, fill=fill)


@pytest.mark.unit
def test_group_grid_by_sections_groups_rows():
    rows = [
        [_cell("orphan")],                                   # dropped: before first section
        [_cell("SecA", bold=True, fill="#A6A6A6")],
        [_cell("a1")],
        [_cell("SecB", bold=True, fill="#A6A6A6")],
        [_cell("b1"), _cell("x")],
        [_cell("b2")],
    ]
    grid = GridSheet(title="T", n_rows=len(rows), n_cols=1, rows=rows)
    groups = group_grid_by_sections(grid)

    assert [label for label, _ in groups] == ["SecA", "SecB"]
    assert len(groups[0][1]) == 2       # header + a1
    assert len(groups[1][1]) == 3       # header + b1 + b2


@pytest.mark.unit
def test_group_grid_by_sections_no_sections_returns_empty():
    grid = GridSheet(title="T", n_rows=2, n_cols=1,
                     rows=[[_cell("x")], [_cell("y")]])
    assert group_grid_by_sections(grid) == []


@pytest.mark.unit
def test_group_grid_by_sections_ignores_bold_without_fill():
    rows = [
        [_cell("not a section", bold=True, fill="#FFFFFF")],
        [_cell("Sec", bold=True, fill="#A6A6A6")],
    ]
    grid = GridSheet(title="T", n_rows=2, n_cols=1, rows=rows)
    groups = group_grid_by_sections(grid)
    assert [label for label, _ in groups] == ["Sec"]


# --------------------------------------------------------------------------
# meta_engine helpers
# --------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected",
    [
        (None, ""),
        (datetime(2025, 6, 30, 12, 30), "2025-06-30"),
        (date(2025, 6, 30), "2025-06-30"),
        ("raw", "raw"),
        (20250630, "20250630"),
    ],
)
def test_fmt_date(value, expected):
    assert _fmt_date(value) == expected


@pytest.mark.unit
def test_build_meta_rows_core_fields_and_warnings():
    ctr = SimpleNamespace(
        scheme=SimpleNamespace(
            scheme_name="Alpha Scheme",
            quote_number=11,
            calculation_date=date(2025, 6, 30),
        )
    )
    assump = SimpleNamespace(basis_snapshot_name=None, snapshot_name=None)
    all_results = {
        "gla": pl.DataFrame({"x": [1]}),
        "lsdb": pl.DataFrame({"x": []}),
        "ci": None,
    }

    rows = build_meta_rows(
        ctr, assump, all_results,
        runtime_s=1.234,
        basis_snapshot="B1",
        assumption_snapshot="A1",
        ctr_path=Path("in.ctr"),
        warnings=["w1", "w2"],
    )
    d = dict(rows)

    assert rows[0] == ("Field", "Value")
    assert d["Scheme Name"] == "Alpha Scheme"
    assert d["Quote Number"] == 11
    assert d["Calculation Date"] == "2025-06-30"
    assert d["CTR File"] == "in.ctr"
    assert d["Engine Version"] == ENGINE_VERSION
    assert d["Basis Snapshot"] == "B1"
    assert d["Assumption Snapshot"] == "A1"
    assert d["Total Runtime (s)"] == 1.23
    assert d["Active Products"] == "GLA"
    assert d["Warnings"] == "2 warning(s)"
    assert d["  #1"] == "w1"
    assert d["  #2"] == "w2"


@pytest.mark.unit
def test_build_meta_rows_falls_back_to_assump_attrs_and_empty():
    ctr = SimpleNamespace(scheme=SimpleNamespace())
    assump = SimpleNamespace(
        basis_snapshot_name="BS",
        snapshot_name="SS",
    )
    rows = build_meta_rows(ctr, assump, {})
    d = dict(rows)
    assert d["Basis Snapshot"] == "BS"
    assert d["Assumption Snapshot"] == "SS"
    assert d["Total Runtime (s)"] == ""
    assert d["Active Products"] == ""
    assert "Warnings" not in d


@pytest.mark.unit
def test_build_meta_rows_top_level_scheme_attrs():
    ctr = SimpleNamespace(scheme_name="Top", quote_number=3,
                          calculation_date=None, scheme=SimpleNamespace())
    rows = build_meta_rows(ctr, None, {}, runtime_s=None)
    d = dict(rows)
    assert d["Scheme Name"] == "Top"
    assert d["Basis Snapshot"] == ""


@pytest.mark.unit
def test_build_engine_frame_empty_and_none():
    df = build_engine_frame({})
    assert df.columns == ["product"]
    assert df.height == 0

    df = build_engine_frame({"gla": None})
    assert df.columns == ["product"]


@pytest.mark.unit
def test_build_engine_frame_concatenates_and_tags_product():
    all_results = {
        "gla": pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]}),
        "ci": pl.DataFrame({"a": [5], "c": ["x"]}),
    }
    df = build_engine_frame(all_results)
    assert df.height == 3
    assert df.columns[0] == "product"
    assert df["product"].to_list() == ["gla", "gla", "ci"]
    assert {"a", "b", "c"} <= set(df.columns)
    assert df.filter(pl.col("product") == "ci")["b"][0] is None


@pytest.mark.unit
def test_build_engine_frame_all_empty_returns_product_only():
    df = build_engine_frame({"gla": pl.DataFrame({"a": []})})
    assert df.columns == ["product"]
    assert df.height == 0


@pytest.mark.unit
def test_write_calc_companion_parquet_roundtrip(tmp_path):
    df = pl.DataFrame({
        "product": ["gla", "gla"],
        "vals": [1.5, 2.5],
    })
    target = tmp_path / "report.xlsx"
    out = write_calc_companion_parquet(df, target)
    assert out == tmp_path / "report_calc.parquet"
    assert out.exists()

    back = pl.read_parquet(out)
    assert back.height == 2
    assert back["product"].to_list() == ["gla", "gla"]


@pytest.mark.unit
@pytest.mark.parametrize("col_name,series", [
    ("listcol", pl.Series("listcol", [[1, 2]], dtype=pl.List(pl.Int64))),
    ("obj", pl.Series("obj", [{"a": 1}], dtype=pl.Object)),
])
def test_write_calc_companion_parquet_unsanitizable_column_limitation(
        tmp_path, col_name, series):
    """Documents a latent bug: the sanitize loop casts Object/List columns
    to Utf8, but polars 1.42 cannot cast either type to String (even with
    strict=False), so the companion writer raises instead of degrading.
    Real engine frames carry neither dtype, so prod never hits this."""
    df = pl.DataFrame({"product": ["gla"]}).with_columns(series)
    with pytest.raises((pl.exceptions.InvalidOperationError,
                        pl.exceptions.ComputeError)):
        write_calc_companion_parquet(df, tmp_path / "report.xlsx")


@pytest.mark.unit
def test_write_meta_sheet_writes_header_and_styles():
    wb = openpyxl.Workbook()
    ws = write_meta_sheet(wb, [("Field", "Value"), ("Scheme Name", "X")])
    assert ws.title == "Meta"
    assert ws["A1"].value == "Field"
    assert ws["A1"].font.bold is True
    assert str(ws["A1"].fill.fgColor.rgb).endswith("305496")
    assert ws["A2"].value == "Scheme Name"
    assert ws["A2"].font.bold is True
    assert ws.freeze_panes == "A2"

    # rewriting replaces the sheet
    ws2 = write_meta_sheet(wb, [("Field", "Value")])
    assert ws2.max_row == 1

    # empty rows still produces the sheet
    ws3 = write_meta_sheet(wb, [], sheet_name="Meta2")
    assert ws3.title == "Meta2"
    assert ws3.max_row == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "col,expected",
    [
        ("date_of_birth", "yyyy-mm-dd"),
        ("annual_salary", "#,##0.00"),
        ("total_cover", "#,##0.00"),
        ("incidence_rate", "0.00000000"),
        ("member_no", "#,##0"),
        ("xyzzy", None),
    ],
)
def test_guess_format(col, expected):
    assert _guess_format(col) == expected


@pytest.mark.unit
def test_write_engine_sheet_empty_and_populated():
    wb = openpyxl.Workbook()
    ws = write_engine_sheet(wb, None)
    assert ws["A1"].value == "(no data)"

    df = pl.DataFrame({
        "product": ["gla"],
        "annual_salary": [1000.0],
        "member_no": [7],
        "label": ["x"],
    })
    ws2 = write_engine_sheet(wb, df)
    assert ws2.title == "Calc - All Products"
    assert [c.value for c in ws2[1]] == ["product", "annual_salary", "member_no", "label"]
    assert ws2.cell(row=2, column=1).value == "gla"
    assert ws2.freeze_panes == "B2"
    assert ws2.auto_filter.ref == ws2.dimensions
    assert ws2.cell(row=1, column=1).font.bold is True

    # same-name rewrite replaces
    ws3 = write_engine_sheet(wb, df)
    assert wb["Calc - All Products"] is ws3
