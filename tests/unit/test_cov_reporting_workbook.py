"""Coverage tests for reporting workbook writers, HTML adapter and writer entry point."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import openpyxl
import polars as pl
import pytest

from reporting import inputs as inp
from reporting.category_breakdown import write_category_breakdown
from reporting.ext_fc_section import apply_ext_fc_column_widths, write_ext_fc_section
from reporting.grid_extract import GridCell, GridSheet, sheet_to_grid
from reporting.grid_sections import group_grid_by_sections
from reporting.html_adapter import (
    build_full_report_html,
    grid_to_html_table,
    grid_to_html_with_product_pills,
)
from reporting.pricing_outputs import _get_product_display_name, write_pricing_outputs
from reporting.writer import _reorder_sheets, write_pricing_report


# --------------------------------------------------------------------------
# shared fixtures (mirrors of the inputs tests)
# --------------------------------------------------------------------------

def _scheme(**kw):
    s = SimpleNamespace(
        scheme_name="Test Scheme",
        quote_number=7,
        calculation_date=date(2025, 6, 30),
        rating={},
        rate_basis_by_product={},
        premium_frequency="Monthly",
        include_vat_commission="Yes",
        country_scale="South Africa",
        intermediary="Broker",
        profit_refund_loading_by_product={},
        express_over_gla={},
        category_split={},
        priced_margin_overrides={},
        fc_paid_up_benefits_active={},
        fc_pup_death_lsdb={},
        fc_pup_lsdb={},
        fc_pup_death={},
        primary="Mining",
        secondary="Gold",
        region="Gauteng",
        education_product=None,
        ext_fc_pct_male={},
        ext_fc_avg_salary={},
        ext_fc_parents_selected={},
        ext_fc_extended_family_selected={},
    )
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _assump():
    tables = {
        "profit_margins": pl.DataFrame({
            "product": ["gla", "gip", "lsdb", "ci", "ti", "adb", "accdb", "dismem"],
            "margin_type": ["standard"] * 8,
            "margin": [0.10, 0.10, 0.08, 0.09, 0.07, 0.05, 0.05, 0.05],
        }),
        "admin_fees": pl.DataFrame({
            "product": ["gla", "lsdb", "lsdb", "ci", "gip", "ext_fc"],
            "variant": ["default", "accelerator", "standalone", "accelerator", "default", "default"],
            "admin": [0.05, 0.04, 0.06, 0.04, 0.05, 0.02],
        }),
        "product_limits": pl.DataFrame({
            "product": ["gip", "lsdb", "ci_new", "educate_a_child", "educate_a_child"],
            "param_name": ["cash_for_loved_ones", "max_cover", "max_cover",
                           "profit_margin", "admin_rate"],
            "param_value": [0.02, 2_000_000.0, 3_000_000.0, 0.12, 0.03],
        }),
        "ci_table_mapping": pl.DataFrame({
            "product_option": ["opt_x"],
            "variant_type": ["New"],
        }),
        "ext_fc_params": pl.DataFrame({
            "param_name": ["rate_basis", "commission_pct"],
            "param_value": [1.0, 0.05],
        }),
    }
    globals_ = {
        "scheme_size_large_threshold": 400,
        "eoh_amount": 10_000.0, "eoh_layer1": 500_000.0, "eoh_layer2": 500_000.0,
        "eoh_perc1": 0.5, "eoh_perc2": 0.3, "eoh_perc3": 0.1,
        "eoh_layer1_cap": None, "eoh_layer2_cap": None,
        "eoh_amount_ci": 8_000.0, "eoh_layer1_ci": 400_000.0, "eoh_layer2_ci": 400_000.0,
        "eoh_perc1_ci": 0.5, "eoh_perc2_ci": 0.3, "eoh_perc3_ci": 0.1,
        "eoh_layer1_cap_ci": None, "eoh_layer2_cap_ci": None,
        "eoh_max_gip": 145_000.0, "eoh_max_tip": 145_000.0,
    }
    return SimpleNamespace(tables=tables, globals=globals_,
                           basis_snapshot_name="BasisSnap", snapshot_name="AssumpSnap")


def _ctr(scheme=None):
    return SimpleNamespace(
        scheme=scheme or _scheme(),
        categories=pl.DataFrame({"product": [], "field": [], "value": []}),
    )


def _mixed_df():
    return pl.DataFrame({
        "product_active": [True, True, True, True],
        "status": ["A", "A", "DB", "A"],
        "age": [30.0, 40.0, 50.0, 70.0],
        "age_nb": [30.0, 40.0, 50.0, 70.0],
        "ceasing_age": [65.0] * 4,
        "gender": ["M", "F", "M", "F"],
        "annual_salary": [100_000.0, 200_000.0, 150_000.0, 120_000.0],
        "total_cover": [300_000.0, 600_000.0, 450_000.0, 360_000.0],
        "category_number": [1, 1, 2, 2],
        "qx": [0.001, 0.002, 0.003, 0.004],
        "basic_technical_premium": [1200.0, 2400.0, 1800.0, 1500.0],
        "aids_technical_premium": [100.0, 200.0, 150.0, 50.0],
        "co_premium": [12.0, 24.0, 18.0, 15.0],
        "ctc_premium": [6.0, 12.0, 9.0, 7.5],
        "waiver_premium": [3.0, 6.0, 4.5, 3.75],
        "prb_premium": [30.0, 60.0, 45.0, 30.0],
        "reinstatement_premium": [0.0] * 4,
        "profit_margin_amount": [120.0, 240.0, 180.0, 150.0],
        "basic_excl_esc": [1100.0, 2200.0, 1650.0, 1400.0],
        "reporting_incidence": [0.01, 0.02, 0.015, 0.02],
        "reporting_incidence_x_cover": [3.0, 12.0, 6.75, 7.2],
    })


def _col_a_labels(ws):
    return [ws.cell(row=r, column=1).value for r in range(1, ws.max_row + 1)]


# --------------------------------------------------------------------------
# write_category_breakdown
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_write_category_breakdown_structure_and_values():
    cb = inp.gather_cb_data({"gla": _mixed_df()}, _scheme(), _assump())
    wb = openpyxl.Workbook()
    ws = write_category_breakdown(wb, cb)

    assert ws.title == "Category Breakdown"
    labels = _col_a_labels(ws)

    # first render-order product is gla -> Life Assurance title at row 2
    assert ws.cell(row=2, column=1).value == "Life Assurance"
    assert "Lump Sum Disability" in labels
    assert "Family Cover" in labels
    assert "Monthly Premium - GLA" in labels
    assert "% of Benefit earnings - GLA" in labels
    assert "Monthly Rate per R1000 Cover - GLA" in labels
    assert "Additional Selections" in labels
    assert "Premium Sub - Total" in labels
    assert "Total Premium" in labels

    # active flag row (row 3): cat1/cat2 active, cat3 inactive
    assert ws.cell(row=3, column=3).value == "Yes"
    assert ws.cell(row=3, column=4).value == "Yes"
    assert ws.cell(row=3, column=5).value == "No"

    # header row: "Monthly Premium - GLA" followed by Scheme Total + Cat 1..20
    hdr = labels.index("Monthly Premium - GLA") + 1
    assert ws.cell(row=hdr, column=2).value == "Scheme Total"
    assert ws.cell(row=hdr, column=3).value == "Cat 1"
    assert ws.cell(row=hdr, column=22).value == "Cat 20"

    # first "Basic Premium" data row -> scheme basic in col 2, cat1 basic in col 3
    bp_row = labels.index("Basic Premium") + 1
    assert ws.cell(row=bp_row, column=2).value == pytest.approx(
        cb["gla"]["scheme"]["basic_premium"])
    assert ws.cell(row=bp_row, column=3).value == pytest.approx(
        cb["gla"]["cats"][1]["basic_premium"])
    mc_row = labels.index("Member count") + 1
    assert ws.cell(row=mc_row, column=3).value == 2

    # second call replaces the sheet
    ws2 = write_category_breakdown(wb, cb)
    assert wb["Category Breakdown"] is ws2


@pytest.mark.unit
def test_write_category_breakdown_empty_data():
    wb = openpyxl.Workbook()
    ws = write_category_breakdown(wb, {})
    labels = _col_a_labels(ws)
    # all 13 render-order sections still get titles
    assert "Life Assurance" in labels
    assert "Educate a Child" in labels
    assert "Group Income Protection" in labels
    assert "Temporary Income Protection" in labels


@pytest.mark.unit
def test_write_category_breakdown_express_and_display_title():
    cb = {
        "gla": {
            "_display_title": "Custom GLA Title",
            "express_over_gla": True,
            "scheme": {"basic_premium": 5.0, "total_salary_annual": 100.0,
                       "total_cover": 50.0},
            "cats": {},
            "active": {},
        },
        "education": {
            "_display_title": "My Edu Product",
            "scheme": {}, "cats": {}, "active": {},
        },
    }
    wb = openpyxl.Workbook()
    ws = write_category_breakdown(wb, cb)
    labels = _col_a_labels(ws)
    assert "Custom GLA Title" in labels
    assert "My Edu Product" in labels
    assert "% of GLA earnings - GLA" in labels


# --------------------------------------------------------------------------
# ext_fc_section
# --------------------------------------------------------------------------

def _ext_fc_data():
    rate_row = {"age_band": "18 to 59", "cover": 50_000.0, "risk_premium": 10.0,
                "profit": 1.0, "admin": 0.5, "initial_commission": 1.0,
                "normal_commission": None, "vat": 1.5, "gross": 14.0}
    return {
        "_display_title": "Extended Family Cover",
        "profit_rate_standard": 0.10, "profit_rate_priced": 0.12,
        "admin_rate_standard": 0.02, "admin_rate_priced": 0.03,
        "rate_basis_standard": 1.0, "rate_basis_priced": 1.1,
        "commission_pct_standard": 0.05, "commission_pct_priced": 0.06,
        "vat_pct": 0.15,
        "categories": {
            1: {"n_members": 2, "salary_bill": 300_000.0, "pct_male": 0.5,
                "avg_salary": 150_000.0, "parents_selected": True,
                "extended_family_selected": True,
                "parents_standard": [dict(rate_row)],
                "parents_priced": [dict(rate_row)],
                "extended_family_standard": [dict(rate_row)],
                "extended_family_priced": [dict(rate_row)]},
            2: {"n_members": 1, "salary_bill": 100_000.0, "pct_male": None,
                "avg_salary": None, "parents_selected": False,
                "extended_family_selected": False,
                "parents_standard": [], "parents_priced": [],
                "extended_family_standard": [], "extended_family_priced": []},
        },
    }


@pytest.mark.unit
def test_write_ext_fc_section():
    wb = openpyxl.Workbook()
    ws = wb.active
    end = write_ext_fc_section(ws, 1, _ext_fc_data())
    assert end > 1
    labels = _col_a_labels(ws)
    assert "Extended Family Cover" in labels
    assert "Assumption" in labels
    assert "Profit Margin" in labels
    assert "Category 1" in labels
    assert "Category 2" in labels
    assert "No. of Members" in labels
    assert "Parents - Standard" in labels
    assert "Parents - Priced" in labels
    assert "Extended Family - Standard" in labels
    assert "Age Band" in labels
    # category 2 has no selections -> no rate tables between its header and EOF
    cat2 = labels.index("Category 2") + 1
    assert ws.cell(row=cat2 + 2, column=1).value == 1  # n_members value row

    apply_ext_fc_column_widths(ws)
    assert ws.column_dimensions["A"].width >= 18


@pytest.mark.unit
def test_write_category_breakdown_includes_ext_fc():
    cb = {"ext_fc": _ext_fc_data()}
    wb = openpyxl.Workbook()
    ws = write_category_breakdown(wb, cb)
    labels = _col_a_labels(ws)
    assert "Extended Family Cover" in labels
    assert "Rate Basis" in labels


# --------------------------------------------------------------------------
# pricing_outputs
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_get_product_display_name():
    assert _get_product_display_name("gla") == "GLA"
    assert _get_product_display_name("lsdb", {"lsdb": "Accelerator"}) == "LSDB Accelerator"
    assert _get_product_display_name("lsdb", {"lsdb": "standalone"}) == "LSDB Standalone"
    assert _get_product_display_name("ci", {"ci": "yes"}) == "CI Standalone"
    assert _get_product_display_name("ci", {"ci": "accelerated"}) == "CI Accelerator"
    assert _get_product_display_name("education", edu_display_name="Edu X") == "Edu X"
    assert _get_product_display_name("unknown_p") == "UNKNOWN_P"
    assert _get_product_display_name("lsdb", None) == "LSDB Standalone"


@pytest.mark.unit
def test_write_pricing_outputs_structure():
    scheme = _scheme()
    po = inp.gather_po_data({"gla": _mixed_df()}, scheme, assump=_assump())
    wb = openpyxl.Workbook()
    ws = write_pricing_outputs(wb, po)

    assert ws.title == "Pricing Outputs"
    assert ws.cell(row=1, column=1).value == "Pricing Adjustments & Review"
    assert ws.cell(row=1, column=3).value == "New Business"

    labels = [ws.cell(row=r, column=2).value for r in range(1, ws.max_row + 1)]
    assert "Express over GLA Salary" in labels
    assert "Run Type" in labels
    assert "Product" in labels
    assert "Industry" in labels
    assert "Membership" in labels
    assert "Standard Margins - % of Net" in labels
    assert "Priced Margins - % of Net" in labels
    assert "Current Gross Rate" in labels
    assert "Recommended Change" in labels

    col_a = _col_a_labels(ws)
    assert "Premium Build-Up: GLA" in col_a
    assert "Premium Build-Up: Dismemberment" in col_a

    # hidden change-component rows exist
    hidden = [r for r in range(1, ws.max_row + 1) if ws.row_dimensions[r].hidden]
    assert len(hidden) > 0

    # express flag row: all absent products -> "No"
    r_expr = labels.index("Express over GLA Salary") + 1
    assert ws.cell(row=r_expr, column=3).value == "No"

    # industry row carries the scheme value
    r_ind = labels.index("Industry") + 1
    assert ws.cell(row=r_ind, column=3).value == "Gold"


@pytest.mark.unit
def test_write_pricing_outputs_with_ext_fc_and_education_title():
    scheme = _scheme(education_product="educate_a_child")
    po = inp.gather_po_data({"gla": _mixed_df(), "educate_a_child": _mixed_df()},
                            scheme, assump=_assump())
    po["ext_fc_data"] = _ext_fc_data()
    wb = openpyxl.Workbook()
    ws = write_pricing_outputs(wb, po)
    col_a = _col_a_labels(ws)
    # registry display name replaces "Education Guardian"
    assert "Premium Build-Up: Educate a Child" in col_a
    assert "Extended Family Cover" in col_a


# --------------------------------------------------------------------------
# html_adapter
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_grid_to_html_table_styles_and_escaping():
    grid = GridSheet(title="t", n_rows=1, n_cols=2, rows=[[
        GridCell(value="a", display="<b>x</b>", bold=True, font_color="FF0000",
                 fill="#A6A6A6", align="center", bordered=True),
        GridCell(value=1, display="1.00"),
    ]])
    html = grid_to_html_table(grid)
    assert html.startswith("<table")
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "background-color:#A6A6A6" in html
    assert "color:#FF0000" in html
    assert "font-weight:bold" in html
    assert "text-align:center" in html
    assert "border:1px solid #000" in html


@pytest.mark.unit
def test_grid_to_html_with_product_pills():
    def c(d, bold=False, fill=None):
        return GridCell(value=d, display=d, bold=bold, fill=fill)
    grid = GridSheet(title="t", n_rows=4, n_cols=1, rows=[
        [c("Life Assurance", bold=True, fill="#A6A6A6")],
        [c("row")],
        [c("Lump Sum Disability", bold=True, fill="#A6A6A6")],
        [c("row2")],
    ])
    html = grid_to_html_with_product_pills(grid)
    assert 'data-pill="cbprod-0"' in html
    assert 'data-pill="cbprod-1"' in html
    assert "Life Assurance" in html and "Lump Sum Disability" in html
    assert 'id="cbprod-0"' in html and 'id="cbprod-1"' in html
    assert 'display:block' in html  # first panel visible
    assert "showPill" in html


@pytest.mark.unit
def test_build_full_report_html_end_to_end():
    scheme = _scheme()
    cb = inp.gather_cb_data({"gla": _mixed_df()}, scheme, _assump())
    po = inp.gather_po_data({"gla": _mixed_df()}, scheme, assump=_assump())
    html = build_full_report_html(cb, po, scheme_name="Test Scheme")
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Test Scheme</title>" in html
    assert "Category Breakdown" in html
    assert "Pricing Outputs" in html
    assert 'class="pillbtn"' in html
    assert "Life Assurance" in html
    assert "panel-po" in html
    # grid sectioning produced pill panels
    assert "cbprod-0" in html


# --------------------------------------------------------------------------
# writer.write_pricing_report + _reorder_sheets
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_write_pricing_report_xlsx(tmp_path):
    ctr = _ctr()
    out = write_pricing_report(
        tmp_path / "report.xlsx",
        {"gla": _mixed_df()},
        ctr,
        assump=_assump(),
        ctr_path=tmp_path / "input.ctr",
        warnings=["heads up"],
        runtime_s=2.5,
    )
    assert out == tmp_path / "report.xlsx"
    assert out.exists()

    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames[:3] == ["Meta", "Category Breakdown", "Pricing Outputs"]

    meta = wb["Meta"]
    fields = {meta.cell(row=r, column=1).value: meta.cell(row=r, column=2).value
              for r in range(1, meta.max_row + 1)}
    assert fields["Scheme Name"] == "Test Scheme"
    assert fields["Warnings"] == "1 warning(s)"

    companion = tmp_path / "report_calc.parquet"
    assert companion.exists()
    back = pl.read_parquet(companion)
    assert back["product"].to_list() == ["gla"] * 4
    assert "basic_technical_premium" in back.columns


@pytest.mark.unit
def test_write_pricing_report_html_only(tmp_path):
    ctr = _ctr()
    out = write_pricing_report(
        tmp_path / "rpt.xlsx",
        {"gla": _mixed_df()},
        ctr,
        assump=_assump(),
        write_xlsx=False,
        write_html=True,
    )
    assert out == tmp_path / "rpt_calc.parquet"
    assert out.exists()
    assert not (tmp_path / "rpt.xlsx").exists()

    html_path = tmp_path / "rpt.html"
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "<html" in html
    assert "Test Scheme" in html


@pytest.mark.unit
def test_write_pricing_report_no_outputs(tmp_path):
    ctr = _ctr()
    target = tmp_path / "none.xlsx"
    out = write_pricing_report(
        target, {"gla": _mixed_df()}, ctr, assump=_assump(),
        write_xlsx=False, write_parquet=False,
    )
    assert out == target
    assert not target.exists()
    assert not (tmp_path / "none_calc.parquet").exists()


@pytest.mark.unit
def test_reorder_sheets():
    wb = openpyxl.Workbook()
    wb.create_sheet("Pricing Outputs")
    wb.create_sheet("Meta")
    wb.create_sheet("Other")
    wb.create_sheet("Category Breakdown")
    _reorder_sheets(wb, ["Meta", "Category Breakdown", "Pricing Outputs"])
    assert wb.sheetnames[:3] == ["Meta", "Category Breakdown", "Pricing Outputs"]
    assert set(wb.sheetnames[3:]) == {"Sheet", "Other"}


# --------------------------------------------------------------------------
# grid pipeline on a real sheet
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_cb_sheet_to_grid_sections_feed_pills():
    cb = inp.gather_cb_data({"gla": _mixed_df()}, _scheme(), _assump())
    wb = openpyxl.Workbook()
    ws = write_category_breakdown(wb, cb)
    grid = sheet_to_grid(ws, title="Category Breakdown")
    assert grid.n_rows > 10
    groups = group_grid_by_sections(grid)
    labels = [g[0] for g in groups]
    assert "Life Assurance" in labels
    # 13 product sections in render order
    assert len(groups) >= 13
    html = grid_to_html_with_product_pills(grid)
    assert "Life Assurance" in html
