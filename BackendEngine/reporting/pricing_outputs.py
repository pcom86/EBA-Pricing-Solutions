from __future__ import annotations
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.styles.cell_style import StyleArray

from .constants import (
    PO_PRODUCT_DISPLAY,
    PO_BUILDUP_TITLE, PO_TECH_TITLE, PO_BASIS_TITLE,
    PO_SALARY_LABEL, PO_RATE_LABEL,
)
from .ext_fc_section import write_ext_fc_section, apply_ext_fc_column_widths


_TITLE_FONT     = Font(bold=True, size=12, color="000000")
_TITLE_FILL     = PatternFill("solid", fgColor="92D050")
_EMPHASIS_FONT  = Font(bold=True, size=10, color="000000")
_EMPHASIS_FILL  = PatternFill("solid", fgColor="D9D9D9")
_SECTION_FONT   = Font(bold=True, color="FFFFFF")
_SECTION_FILL   = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT    = Font(bold=True)
_HEADER_FILL    = PatternFill("solid", fgColor="D9E5F1")
_TOTAL_FONT     = Font(bold=True)
_TOTAL_FILL     = PatternFill("solid", fgColor="F2F2F2")

_GREY_NOBORDER_FILL = PatternFill("solid", fgColor="A6A6A6")
_GREEN_FILL         = PatternFill("solid", fgColor="92D050")
_YELLOW_FILL        = PatternFill("solid", fgColor="FFFF00")
_LIGHTGREEN_FILL    = PatternFill("solid", fgColor="B5E6A2")
_PALEGREEN_FILL     = PatternFill("solid", fgColor="DAF2D0")
_ALT_FILL_A         = PatternFill("solid", fgColor="D9D9D9")
_ALT_FILL_B         = PatternFill("solid", fgColor="FFFFFF")

_BOLD          = Font(bold=True)
_BOLD_WHITE    = Font(bold=True, color="FFFFFF")

_LEFT   = Alignment(horizontal="left",   vertical="center")
_CENTER = Alignment(horizontal="center", vertical="center")

_THIN_SIDE   = Side(style="thin", color="000000")
_FULL_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)


_MARGIN_ROW_LABELS: tuple[tuple[str, str, str], ...] = (
    ("Conversion Option",                     "conversion_option", "pct6"),
    ("Cover To Continue",                     "cover_to_continue", "pct6"),
    ("PUP Cost",                              "pup_cost",          "pct6"),
    ("Cash for Loved Ones",                   "cash_for_loved_ones", "pct6"),
    ("Profit Refund Option Loading",          "pro_loading",       "pct6"),
    ("Profit Margin",                         "profit_margin",     "pct6"),
    ("Admin",                                 "admin",             "pct6"),
    ("Business Decision Margin",              "business_decision", "pct6"),
    ("Target Unchanged/Phasing Margin",       "phasing",           "pct6"),
    ("Reinsurance Profit",                    "reins_profit",      "pct6"),
    ("Reinsurance Arbitrage/Cost Margin",     "reins_arb",         "pct6"),
    ("Rate Basis",                            "rate_basis",        "num"),
)


def _get_style_cache(wb):
    cache = getattr(wb, "_cb_fast_style_cache", None)
    if cache is None:
        cache = {"font": {}, "fill": {}, "border": {}, "alignment": {}}
        wb._cb_fast_style_cache = cache
    return cache


def _resolve_idx(coll, cache_dict, obj):
    key = id(obj)
    idx = cache_dict.get(key)
    if idx is None:
        idx = coll.add(obj)
        cache_dict[key] = idx
    return idx


def _apply_style(cell, font=None, fill=None, border=None, alignment=None):
    wb = cell.parent.parent
    cache = _get_style_cache(wb)
    if cell._style is None:
        cell._style = StyleArray()
    if font is not None:
        cell._style.fontId = _resolve_idx(wb._fonts, cache["font"], font)
    if fill is not None:
        cell._style.fillId = _resolve_idx(wb._fills, cache["fill"], fill)
    if border is not None:
        cell._style.borderId = _resolve_idx(wb._borders, cache["border"], border)
    if alignment is not None:
        cell._style.alignmentId = _resolve_idx(wb._alignments, cache["alignment"], alignment)


def write_pricing_outputs(
    wb,
    po_data: dict,
    sheet_name: str = "Pricing Outputs",
):
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)

    row = _write_block_a(ws, 1, po_data)

    _edu_profile = (po_data.get("profile") or {}).get("education") or {}
    _edu_display_name = _edu_profile.get("_display_title")
    _buildup = dict(po_data["buildup"])
    if "education" in _buildup and _edu_display_name:
        _buildup["education"] = {**_buildup["education"], "_edu_display_name": _edu_display_name}

    row += 4
    for pkey in po_data["products"]:
        row = _write_buildup(ws, row, pkey, _buildup[pkey])
        row += 3

    ext_fc_data = po_data.get("ext_fc_data")
    if ext_fc_data:
        row = write_ext_fc_section(ws, row, ext_fc_data)
        row += 2

    _apply_widths(ws, len(po_data["products"]))
    apply_ext_fc_column_widths(ws)
    return ws


def _write_block_a(ws, start_row: int, po: dict) -> int:
    products       = po["products"]
    profile        = po["profile"]
    eoh            = po["eoh"]
    margins        = po["margins"]
    priced_margins = po.get("priced_margins", margins)
    express        = po["express"]
    scheme         = po["scheme"]
    ratings        = po.get("ratings", {})

    ws.cell(row=start_row, column=1, value="Pricing Adjustments & Review")
    ws.cell(row=start_row, column=3, value="New Business")
    for _col in range(1, 27):
        _c = ws.cell(row=start_row, column=_col)
        _apply_style(_c, font=_BOLD, fill=_GREY_NOBORDER_FILL)

    r = start_row + 2

    b_cell = ws.cell(row=r, column=2, value="Express over GLA Salary")
    _apply_style(b_cell, font=_BOLD_WHITE, fill=_GREEN_FILL, border=_FULL_BORDER, alignment=_LEFT)
    for i, pkey in enumerate(products):
        c = ws.cell(row=r, column=3 + i, value="Yes" if express.get(pkey) else "No")
        _apply_style(c, fill=_YELLOW_FILL, border=_FULL_BORDER, alignment=_CENTER)
    r += 1

    b_cell2 = ws.cell(row=r, column=2, value="Run Type")
    _apply_style(b_cell2, font=_BOLD_WHITE, fill=_GREEN_FILL, border=_FULL_BORDER, alignment=_LEFT)
    c4 = ws.cell(row=r, column=3, value=scheme.get("run_type", "Yes"))
    _apply_style(c4, fill=_YELLOW_FILL, border=_FULL_BORDER, alignment=_CENTER)
    r += 2

    edu_display_name = (profile.get("education") or {}).get("_display_title")

    _write_product_header(ws, r, products, scheme, ratings=ratings, edu_display_name=edu_display_name)
    r += 1

    r = _write_static_row(ws, r, "Industry", products, lambda p: scheme.get("industry", ""))
    r = _write_static_row(ws, r, "Region",   products, lambda p: scheme.get("region", ""))
    r += 1

    r = _write_profile_rows(ws, r, products, profile, [
        ("Membership",  "membership",  "int"),
        ("-Over NRA",   "over_nra",    "int"),
        ("-Disabled",   "disabled",    "int"),
        ("Average Age", "avg_age",     "num"),
        ("% Males",     "pct_males",   "pct6"),
    ])
    r += 1

    r = _write_profile_rows(ws, r, products, profile, [
        ("Salary Bill",            "salary_bill",       "money"),
        ("Average Salary",         "avg_salary",        "money"),
        ("Age Weighted by Salary", "age_wtd_by_salary", "num"),
    ])
    r += 1

    r = _write_profile_rows(ws, r, products, profile, [
        ("Total Cover",           "total_cover",       "money"),
        ("Total Cover - Main Member", "total_cover_main", "money"),
        ("Average Multiple",      "avg_multiple",      "num"),
        ("Average Cover",         "avg_cover",         "money"),
        ("Average Cover - Main Member", "avg_cover_main", "money"),
        ("Age Weighted by Cover", "age_wtd_by_cover",  "num"),
    ])
    r += 1

    r = _write_profile_rows(ws, r, products, profile, [
        ("Incidence Rate",       "incidence_rate",       "num"),
        ("Expected # Claims",    "expected_claims",      "num"),
        ("Average Cover",        "avg_cover",            "money"),
        ("Expected Claim Size",  "expected_claim_size",  "money"),
        ("Ave Claim/Ave Cover",  "claim_cover_ratio",    "num"),
    ])
    r += 1

    r = _write_profile_rows(ws, r, products, eoh, [
        ("Tech EOH",              "tech_eoh",     "money"),
        ("Enter Custom EOH (always custom EOH < Tech EOH)", "custom_eoh", "money"),
        ("EOH Limit",             "eoh_limit",    "money"),
        ("Members over EOH",      "members_over", "int"),
        ("Cover over EOH",        "cover_over",   "money"),
        ("Max Cover",             "max_cover",    "money"),
    ])
    r += 2

    r = _write_margins_section(
        ws, r, products, margins,
        section_title="Standard Margins - % of Net",
        ratings=ratings,
        data_fill=_PALEGREEN_FILL,
        edu_display_name=edu_display_name,
    )
    r += 1

    r = _write_margins_section(
        ws, r, products, priced_margins,
        section_title="Priced Margins - % of Net",
        ratings=ratings,
        data_fill=_YELLOW_FILL,
        edu_display_name=edu_display_name,
    )
    r += 1

    _hide_start = r
    r = _write_change_component(ws, r, products)
    for _hr in range(_hide_start, r):
        ws.row_dimensions[_hr].hidden = True
    return r


def _write_product_header(ws, r: int, products, scheme, ratings=None, edu_display_name=None) -> None:
    b_cell = ws.cell(row=r, column=2, value="Product")
    _apply_style(b_cell, font=_BOLD_WHITE, fill=_GREEN_FILL, border=_FULL_BORDER, alignment=_LEFT)

    for i, pkey in enumerate(products):
        c = ws.cell(
            row=r,
            column=3 + i,
            value=_get_product_display_name(pkey, ratings, edu_display_name=edu_display_name)
        )
        _apply_style(c, font=_BOLD_WHITE, fill=_GREEN_FILL, border=_FULL_BORDER, alignment=_CENTER)


def _write_static_row(ws, r: int, label: str, products, value_fn) -> int:
    b_cell = ws.cell(row=r, column=2, value=label)
    _apply_style(b_cell, fill=_LIGHTGREEN_FILL, border=_FULL_BORDER, alignment=_LEFT)
    for i, pkey in enumerate(products):
        c = ws.cell(row=r, column=3 + i, value=value_fn(pkey))
        _apply_style(c, fill=_LIGHTGREEN_FILL, border=_FULL_BORDER, alignment=_CENTER)
    return r + 1


def _write_profile_rows(ws, r: int, products, data: dict, rows) -> int:
    for row_idx, (label, key, kind) in enumerate(rows):
        b_cell = ws.cell(row=r, column=2, value=label)
        _apply_style(b_cell, fill=_LIGHTGREEN_FILL, border=_FULL_BORDER, alignment=_LEFT)
        if row_idx == 0:
            _apply_style(b_cell, font=_BOLD)
        for i, pkey in enumerate(products):
            val = (data.get(pkey) or {}).get(key, 0)
            _write_val(ws, r, 3 + i, val, kind)
            c = ws.cell(row=r, column=3 + i)
            _apply_style(c, fill=_LIGHTGREEN_FILL, border=_FULL_BORDER, alignment=_CENTER)
        r += 1
    return r


def _write_margins_section(
    ws,
    r: int,
    products,
    margins: dict,
    section_title: str,
    ratings=None,
    data_fill=None,
    edu_display_name=None,
) -> int:
    _title_cell = ws.cell(row=r, column=2, value=section_title)
    _apply_style(_title_cell, font=_HEADER_FONT)
    r += 1

    b_header = ws.cell(row=r, column=2, value="")
    _apply_style(b_header, font=_BOLD_WHITE, fill=_GREEN_FILL, alignment=_CENTER, border=_FULL_BORDER)

    for i, pkey in enumerate(products):
        c = ws.cell(
            row=r,
            column=3 + i,
            value=_get_product_display_name(pkey, ratings, edu_display_name=edu_display_name),
        )
        _apply_style(c, font=_BOLD_WHITE, fill=_GREEN_FILL, alignment=_CENTER, border=_FULL_BORDER)
    r += 1

    fill = data_fill or _PALEGREEN_FILL
    for label, key, kind in _MARGIN_ROW_LABELS:
        b_cell = ws.cell(row=r, column=2, value=label)
        _apply_style(b_cell, fill=_LIGHTGREEN_FILL, alignment=_LEFT, border=_FULL_BORDER)
        for i, pkey in enumerate(products):
            val = margins.get(pkey, {}).get(key, 0)
            c = ws.cell(row=r, column=3 + i, value=val)
            c.number_format = '0.00%;-0.00%;"-"'
            _apply_style(c, fill=fill, alignment=_CENTER, border=_FULL_BORDER)
        r += 1

    return r


def _write_change_component(ws, r: int, products) -> int:
    n = len(products)

    _c1 = ws.cell(row=r, column=2, value="Current Gross Rate")
    _apply_style(_c1, font=_HEADER_FONT)
    r += 2

    _c2 = ws.cell(row=r, column=2, value="Change Component Contributor")
    _apply_style(_c2, font=_HEADER_FONT)
    r += 1
    for label in (
        "Pricing Correction",
        "Basis Change",
        "Basis + Pricing Correction",
        "Profile Change",
        "Total Change",
    ):
        ws.cell(row=r, column=2, value=label)
        for i in range(n):
            _write_val(ws, r, 3 + i, 0.0, "pct6")
        r += 1
    r += 1

    _c3 = ws.cell(row=r, column=2, value="Recommended Change")
    _apply_style(_c3, font=_HEADER_FONT)
    r += 2

    ws.cell(row=r, column=2, value="Max decrease")
    for i in range(n):
        _write_val(ws, r, 3 + i, -0.075, "pct6")
    r += 1

    ws.cell(row=r, column=2, value="Max Increase")
    for i in range(n):
        _write_val(ws, r, 3 + i, 0.15, "pct6")
    r += 2

    _c4 = ws.cell(row=r, column=2, value="Summary")
    _apply_style(_c4, font=_HEADER_FONT)
    r += 1
    for label in (
        "Theoretical Change",
        "Recommended (Capped) Change",
        "Actual Change",
    ):
        ws.cell(row=r, column=2, value=label)
        for i in range(n):
            _write_val(ws, r, 3 + i, 0.0, "pct6")
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="Target Rate")
    for i in range(n):
        ws.cell(row=r, column=3 + i, value="No")
    r += 1

    return r


def _write_buildup(ws, start_row: int, pkey: str, bu_data: dict) -> int:
    for _col in range(1, 27):
        _c = ws.cell(row=start_row, column=_col)
        _apply_style(_c, font=_BOLD, fill=_GREY_NOBORDER_FILL)
    _edu_name = bu_data.get("_edu_display_name") if pkey == "education" else None
    _buildup_title = (
        PO_BUILDUP_TITLE[pkey].replace("Education Guardian", _edu_name)
        if _edu_name else PO_BUILDUP_TITLE[pkey]
    )
    ws.cell(row=start_row, column=1, value=_buildup_title)
    r = start_row + 2

    salary     = bu_data.get("salary", 0.0)
    cover      = bu_data.get("cover",  0.0)
    tech_rows  = bu_data.get("tech",   [])
    basis_rows = bu_data.get("basis",  [])

    _tech_title = (
        PO_TECH_TITLE[pkey].replace("Education Guardian", _edu_name)
        if _edu_name else PO_TECH_TITLE[pkey]
    )
    tech_title_cell = ws.cell(row=r, column=2, value=_tech_title)
    _apply_style(tech_title_cell, font=_BOLD_WHITE, fill=_GREEN_FILL, alignment=_CENTER)
    r = _write_buildup_header(ws, r, pkey, include_gross=False)
    r = _write_buildup_rows(ws, r, tech_rows, salary, cover, include_gross=False)
    r += 2

    _rb_for_title = bu_data.get("rate_basis")
    if _rb_for_title is None:
        _basis_title = PO_BASIS_TITLE[pkey]
    else:
        _prefix = _tech_title.split(" - Tech Rates")[0].split(" - Standard")[0]
        _basis_title = f"{_prefix} - {round(_rb_for_title * 100)}% Basis"
    basis_title_cell = ws.cell(row=r, column=2, value=_basis_title)
    _apply_style(basis_title_cell, font=_BOLD_WHITE, fill=_GREEN_FILL, alignment=_CENTER)
    r = _write_buildup_header(ws, r, pkey, include_gross=True)
    r = _write_buildup_rows(ws, r, basis_rows, salary, cover, include_gross=True)
    return r


def _write_buildup_header(ws, r: int, pkey: str, include_gross: bool) -> int:
    labels = [
        "Rand Amount",
        PO_SALARY_LABEL[pkey],
        PO_RATE_LABEL[pkey],
        "Cumulative",
        "% of Net",
    ]
    if include_gross:
        labels.append("% of Gross")

    for i, lbl in enumerate(labels):
        c = ws.cell(row=r, column=3 + i, value=lbl)
        _apply_style(c, font=_BOLD_WHITE, fill=_GREEN_FILL, alignment=_CENTER)

    return r + 1


def _write_buildup_rows(ws, r, rows, salary, cover, include_gross):
    if not rows:
        return r

    net = rows[0][1] or 0.0

    if include_gross:
        components  = rows[:-1]
        gross_total = sum(v for _, v, _ in components)
    else:
        components  = rows
        gross_total = 0.0

    cumulative = 0.0
    _money_fmt = 'R #,##0.00;R -#,##0.00;"-"'
    _pct3_fmt  = '0.000%;-0.000%;"-"'
    _num3_fmt  = '0.000;-0.000;"-"'
    _pct2_fmt  = '0.00%;-0.00%;"-"'

    def _write_formatted(col, val, fmt):
        c = ws.cell(row=r, column=col, value=val)
        c.number_format = fmt
        _apply_style(c, alignment=_CENTER, border=_FULL_BORDER)
        return c

    alt_idx = 1

    for row_idx, (label, val, pct_net_override) in enumerate(components):
        cumulative += val
        pct_salary = val / salary if salary else 0.0
        rate_1000  = (val * 1000.0) / cover / 12.0 if cover else 0.0
        pct_net    = pct_net_override if pct_net_override is not None else (val / net if net else 0.0)

        label_cell = ws.cell(row=r, column=2, value=label)
        _apply_style(label_cell, alignment=_CENTER, border=_FULL_BORDER)

        _write_formatted(3, val,        _money_fmt)
        _write_formatted(4, pct_salary, _pct3_fmt)
        _write_formatted(5, rate_1000,  _num3_fmt)
        _write_formatted(6, cumulative, _money_fmt)
        _write_formatted(7, pct_net,    _pct2_fmt)

        if include_gross:
            pct_gross = val / gross_total if gross_total else 0.0
            _write_formatted(8, pct_gross, _pct2_fmt)

        max_col = 8 if include_gross else 7

        if row_idx == 0:
            _apply_style(label_cell, font=_EMPHASIS_FONT, fill=_EMPHASIS_FILL)
            for c in range(3, max_col + 1):
                cell = ws.cell(row=r, column=c)
                _apply_style(cell, font=_EMPHASIS_FONT, fill=_EMPHASIS_FILL)
        else:
            alt_fill = _ALT_FILL_A if alt_idx % 2 == 0 else _ALT_FILL_B
            _apply_style(label_cell, fill=alt_fill)
            for c in range(3, max_col + 1):
                _apply_style(ws.cell(row=r, column=c), fill=alt_fill)
            alt_idx += 1

        r += 1

    if include_gross:
        gross_label, gross_val, _ = rows[-1]
        pct_salary = gross_val / salary if salary else 0.0
        rate_1000  = (gross_val * 1000.0) / cover / 12.0 if cover else 0.0
        pct_net    = gross_val / net if net else 0.0

        label_cell = ws.cell(row=r, column=2, value=gross_label)
        _apply_style(label_cell, alignment=_CENTER, border=_FULL_BORDER)
        _write_formatted(3, gross_val, _money_fmt)
        _write_formatted(4, pct_salary, _pct3_fmt)
        _write_formatted(5, rate_1000,  _num3_fmt)
        _write_formatted(6, gross_val, _money_fmt)
        _write_formatted(7, pct_net,   _pct2_fmt)
        _write_formatted(8, 1.0,       _pct2_fmt)

        alt_fill = _ALT_FILL_A if alt_idx % 2 == 0 else _ALT_FILL_B
        _apply_style(label_cell, fill=alt_fill)
        for c in range(3, 9):
            _apply_style(ws.cell(row=r, column=c), fill=alt_fill)

        r += 1

    return r


def _write_val(ws, row: int, col: int, val, kind: str) -> None:
    cell = ws.cell(row=row, column=col, value=val)
    if   kind == "money": cell.number_format = 'R #,##0;R -#,##0;"-"'
    elif kind == "int":   cell.number_format = '#,##0;-#,##0;"-"'
    elif kind == "num":   cell.number_format = '0.0000;-0.0000;"-"'
    elif kind == "pct6":  cell.number_format = '0.0000%;-0.0000%;"-"'
    elif kind == "pct8":  cell.number_format = '0.000000%;-0.000000%;"-"'


def _apply_widths(ws, n_products: int, min_width: float = 10.0, max_width: float = 45.0) -> None:
    for col_cells in ws.iter_cols():
        col_letter = col_cells[0].column_letter
        if col_letter == "A":
            ws.column_dimensions["A"].width = 7.5
            continue
        longest = 0
        for cell in col_cells:
            if cell.value is None:
                continue
            length = len(str(cell.value))
            if length > longest:
                longest = length
        width = max(min_width, min(longest + 2, max_width))
        ws.column_dimensions[col_letter].width = width


def _get_product_display_name(pkey: str, ratings: dict = None, edu_display_name: str = None) -> str:
    if pkey == "lsdb":
        lsdb_val = str((ratings or {}).get("lsdb", "")).strip().lower()
        if lsdb_val in ("accelerator", "accelerated"):
            return "LSDB Accelerator"
        elif lsdb_val in ("standalone", "yes"):
            return "LSDB Standalone"
    if pkey == "ci":
        ci_val = str((ratings or {}).get("ci", "")).strip().lower()
        if ci_val in ("accelerator", "accelerated"):
            return "CI Accelerator"
        elif ci_val in ("standalone", "yes"):
            return "CI Standalone"
    if pkey == "education" and edu_display_name:
        return edu_display_name
    return PO_PRODUCT_DISPLAY.get(pkey, pkey.upper())
