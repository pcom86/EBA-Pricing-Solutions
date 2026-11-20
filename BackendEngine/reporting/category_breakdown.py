from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.cell_style import StyleArray

from .constants import (
    CB_ADDONS,
    CB_LABEL_SUFFIX,
    CB_PRODUCTS,
    CB_RENDER_ORDER,
    CB_SECTION_TITLE,
    N_CATEGORIES,
)
from .ext_fc_section import apply_ext_fc_column_widths, write_ext_fc_section

_SECTION_FONT      = Font(bold=True, size=12, color="FFFFFF")
_SECTION_FILL      = PatternFill("solid", fgColor="A6A6A6")

_HIGHLIGHT_FONT    = Font(bold=True, color="FFFFFF")
_HIGHLIGHT_FILL    = PatternFill("solid", fgColor="92D050")
_SUBHEADER_FONT    = _HIGHLIGHT_FONT
_SUBHEADER_FILL    = _HIGHLIGHT_FILL
_TOTAL_FONT        = _HIGHLIGHT_FONT
_TOTAL_FILL        = _HIGHLIGHT_FILL

_TABLE_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TABLE_HEADER_FILL = PatternFill("solid", fgColor="92D050")
_ITALIC            = Font(italic=True)
_COL2_BOLD         = Font(bold=True)

_THIN_SIDE   = Side(style="thin", color="000000")
_CELL_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)

_CENTER = Alignment(horizontal="center", vertical="center")


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


def write_category_breakdown(
    wb,
    cb_data: dict[str, dict],
    sheet_name: str = "Category Breakdown",
):
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)

    gla_prod = cb_data.get("gla", {}) or {}
    gla_scheme = gla_prod.get("scheme") or {}
    gla_cats   = gla_prod.get("cats") or {}

    gla_refs = {
        "scheme_salary": float(gla_scheme.get("total_salary_annual", 0.0) or 0.0),
        "scheme_cover":  float(gla_scheme.get("total_cover", 0.0) or 0.0),
        "cats_salary":   {c: float(m.get("total_salary_annual", 0.0) or 0.0)
                          for c, m in gla_cats.items()},
        "cats_cover":    {c: float(m.get("total_cover", 0.0) or 0.0)
                          for c, m in gla_cats.items()},
    }

    row = 2
    for pkey in CB_RENDER_ORDER:
        row = _write_product_section(ws, row, pkey, cb_data.get(pkey, {}), gla_refs)
        row += 2

    ext_fc_data = cb_data.get("ext_fc")
    if ext_fc_data:
        row = write_ext_fc_section(ws, row, ext_fc_data)
        row += 2

    _apply_column_widths(ws)
    apply_ext_fc_column_widths(ws)
    return ws


def _write_product_section(ws, start_row: int, pkey: str, prod_data: dict,
                            gla_refs: dict) -> int:
    title  = prod_data.get("_display_title") or CB_SECTION_TITLE[pkey]
    suffix = CB_LABEL_SUFFIX[pkey]
    addons = CB_ADDONS[pkey]

    express = prod_data.get("express_over_gla", False)
    earnings_label = "% of GLA earnings" if express else "% of Benefit earnings"

    scheme = prod_data.get("scheme", {})
    cats   = prod_data.get("cats", {})
    active = prod_data.get("active", {})
    freq_divisor = float(prod_data.get("freq_divisor", 12.0) or 12.0)

    if express:
        denom_salary_scheme = gla_refs["scheme_salary"]
        denom_salary_by_cat = gla_refs["cats_salary"]
    else:
        denom_salary_scheme = float(scheme.get("total_salary_annual", 0.0) or 0.0)
        denom_salary_by_cat = {c: float(m.get("total_salary_annual", 0.0) or 0.0)
                               for c, m in cats.items()}

    denom_cover_scheme = float(scheme.get("total_cover", 0.0) or 0.0)
    denom_cover_by_cat = {c: float(m.get("total_cover", 0.0) or 0.0)
                          for c, m in cats.items()}
    gla_cover_scheme = gla_refs["scheme_cover"]
    gla_cover_by_cat = gla_refs["cats_cover"]

    _title_cell = ws.cell(row=start_row, column=1, value=title)
    _apply_style(_title_cell, font=_SECTION_FONT, fill=_SECTION_FILL, alignment=_CENTER)
    for _col in range(2, 27):
        _c = ws.cell(row=start_row, column=_col)
        _apply_style(_c, font=_SECTION_FONT, fill=_SECTION_FILL, alignment=_CENTER)
    row = start_row + 1

    row = _write_active_flag_row(ws, row, active)
    row = _write_header_row(ws, row, f"Monthly Premium - {suffix}")
    row = _write_full_table(ws, row, scheme, cats, addons)
    row += 2

    row = _write_active_flag_row(ws, row, active)
    row = _write_header_row(ws, row, f"{earnings_label} - {suffix}")
    row = _write_ratio_table(
        ws, row, scheme, cats, addons,
        denominator="salary",
        denom_scheme=denom_salary_scheme,
        denom_by_cat=denom_salary_by_cat,
        freq_divisor=freq_divisor,
    )
    row += 2

    row = _write_active_flag_row(ws, row, active)
    row = _write_header_row(ws, row, f"Monthly Rate per R1000 Cover - {suffix}")
    row = _write_ratio_table(
        ws, row, scheme, cats, addons,
        denominator="cover",
        denom_scheme=denom_cover_scheme,
        denom_by_cat=denom_cover_by_cat,
        freq_divisor=freq_divisor,
        gla_denom_scheme=gla_cover_scheme,
        gla_denom_by_cat=gla_cover_by_cat,
        express=express,
    )
    return row


def _write_active_flag_row(ws, row: int, active: dict) -> int:
    for i in range(N_CATEGORIES):
        cell = ws.cell(row=row, column=3 + i,
                       value="Yes" if active.get(i + 1, False) else "No")
        _apply_style(cell, alignment=_CENTER)
    return row + 1


def _write_header_row(ws, row: int, title: str) -> int:
    label_cell = ws.cell(row=row, column=1, value=title)
    _apply_style(label_cell, font=_TABLE_HEADER_FONT, fill=_TABLE_HEADER_FILL,
                 border=_CELL_BORDER, alignment=_CENTER)

    scheme_cell = ws.cell(row=row, column=2, value="Scheme Total")
    _apply_style(scheme_cell, font=_TABLE_HEADER_FONT, fill=_TABLE_HEADER_FILL,
                 border=_CELL_BORDER, alignment=_CENTER)

    for i in range(N_CATEGORIES):
        cell = ws.cell(row=row, column=3 + i, value=f"Cat {i + 1}")
        _apply_style(cell, font=_TABLE_HEADER_FONT, fill=_TABLE_HEADER_FILL,
                     border=_CELL_BORDER, alignment=_CENTER)
    return row + 1


def _write_full_table(ws, row: int, scheme: dict, cats: dict, addons) -> int:
    rows = [
        ("Basic Premium",        lambda m: m.get("basic_premium", 0.0),       "money"),
        ("Total Salary pa R",    lambda m: m.get("total_salary_annual", 0.0), "money"),
        ("Member count",         lambda m: m.get("n_members", 0),             "int"),
        ("Salary weighted age",  lambda m: m.get("salary_weighted_age", 0.0), "age"),
        ("Total Cover",          lambda m: m.get("total_cover", 0.0),         "money"),
        ("Cover weighted age",   lambda m: m.get("cover_weighted_age", 0.0),  "age"),
        ("Percentage MALES",     lambda m: m.get("pct_males", 0.0),           "pct"),
        ("Additional Selections", None, "header"),
    ]
    for label, engine_col in addons:
        rows.append((label, lambda m, c=engine_col: m.get(c, 0.0), "money"))
    rows += [
        ("Premium Sub - Total", lambda m: m.get("sub_total", 0.0),     "money"),
        ("Commission ",         lambda m: m.get("commission", 0.0),    "money"),
        ("VAT @ 15%",           lambda m: m.get("vat", 0.0),           "money"),
        ("Total Premium",       lambda m: m.get("total_premium", 0.0), "money"),
    ]

    for label, fn, kind in rows:
        _write_data_row(ws, row, label, fn, kind, scheme, cats,
                        is_total=(label.strip() in ("Premium Sub - Total", "Total Premium")))
        row += 1
    return row


def _write_ratio_table(ws, row: int, scheme: dict, cats: dict, addons,
                       denominator: str,
                       denom_scheme: float,
                       denom_by_cat: dict,
                       freq_divisor: float,
                       gla_denom_scheme: float = None,
                       gla_denom_by_cat: dict = None,
                       express: bool = False) -> int:
    _GLA_COMPONENTS = {"commission", "vat"}
    _use_gla = (denominator == "cover" and express
                and gla_denom_by_cat is not None)

    _kind = "pct3" if denominator == "salary" else "rate3"

    def _den_scheme(comp):
        if _use_gla and comp in _GLA_COMPONENTS:
            return gla_denom_scheme
        return denom_scheme

    def _den_cat(comp, cat_no):
        if _use_gla and comp in _GLA_COMPONENTS:
            return gla_denom_by_cat.get(cat_no, 0.0)
        return denom_by_cat.get(cat_no, 0.0)

    def _ratio(val_periodic, denom):
        if not denom:
            return 0.0
        if denominator == "salary":
            return (val_periodic * freq_divisor) / denom
        return (val_periodic * 1000.0) / denom

    def _scheme_val(m, comp):
        return _ratio(m.get(comp, 0.0), _den_scheme(comp))

    def _cat_val(m, comp, cat_no):
        return _ratio(m.get(comp, 0.0), _den_cat(comp, cat_no))

    def _scheme_total(m):
        if _use_gla:
            return (_scheme_val(m, "sub_total")
                    + _scheme_val(m, "commission")
                    + _scheme_val(m, "vat"))
        return _scheme_val(m, "total_premium")

    def _cat_total(m, cat_no):
        if _use_gla:
            return (_cat_val(m, "sub_total", cat_no)
                    + _cat_val(m, "commission", cat_no)
                    + _cat_val(m, "vat", cat_no))
        return _cat_val(m, "total_premium", cat_no)

    rows = [
        ("Basic Premium", lambda m: _scheme_val(m, "basic_premium"),
         lambda m, i: _cat_val(m, "basic_premium", i), _kind),
        ("Additional Selections", None, None, "header"),
    ]
    for label, engine_col in addons:
        rows.append((
            label,
            lambda m, c=engine_col: _scheme_val(m, c),
            lambda m, i, c=engine_col: _cat_val(m, c, i),
            _kind,
        ))
    rows += [
        ("Premium Sub-Total",
         lambda m: _scheme_val(m, "sub_total"),
         lambda m, i: _cat_val(m, "sub_total", i), _kind),
        ("Commission ",
         lambda m: _scheme_val(m, "commission"),
         lambda m, i: _cat_val(m, "commission", i), _kind),
        ("VAT @ 15%",
         lambda m: _scheme_val(m, "vat"),
         lambda m, i: _cat_val(m, "vat", i), _kind),
        ("Total Premium",
         lambda m: _scheme_total(m),
         lambda m, i: _cat_total(m, i), _kind),
    ]

    for label, scheme_fn, cat_fn, kind in rows:
        is_total = label.strip() in ("Premium Sub-Total", "Total Premium")
        _write_ratio_row(
            ws, row, label, scheme_fn, cat_fn, kind,
            scheme, cats, is_total,
        )
        row += 1

    return row


def _write_ratio_row(ws, row: int, label: str,
                     scheme_fn, cat_fn, kind: str,
                     scheme: dict, cats: dict, is_total: bool) -> None:
    label_cell = ws.cell(row=row, column=1, value=label)
    _apply_style(label_cell, border=_CELL_BORDER, alignment=_CENTER)
    if kind == "header":
        _apply_style(label_cell, font=_SUBHEADER_FONT, fill=_SUBHEADER_FILL)
        for _col in range(2, 3 + N_CATEGORIES):
            _c = ws.cell(row=row, column=_col)
            _apply_style(_c, font=_SUBHEADER_FONT, fill=_SUBHEADER_FILL,
                         border=_CELL_BORDER, alignment=_CENTER)
        return
    if is_total:
        _apply_style(label_cell, font=_TOTAL_FONT, fill=_TOTAL_FILL)

    _write_val(ws, row, 2, scheme_fn(scheme), kind, is_total)

    for i in range(N_CATEGORIES):
        cat_no = i + 1
        m = cats.get(cat_no, {})
        _write_val(ws, row, 3 + i, cat_fn(m, cat_no), kind, is_total)


def _write_data_row(ws, row: int, label: str, fn, kind: str,
                    scheme: dict, cats: dict, is_total: bool) -> None:
    label_cell = ws.cell(row=row, column=1, value=label)
    _apply_style(label_cell, border=_CELL_BORDER, alignment=_CENTER)
    if kind == "header":
        _apply_style(label_cell, font=_SUBHEADER_FONT, fill=_SUBHEADER_FILL)
        for _col in range(2, 3 + N_CATEGORIES):
            _c = ws.cell(row=row, column=_col)
            _apply_style(_c, font=_SUBHEADER_FONT, fill=_SUBHEADER_FILL,
                         border=_CELL_BORDER, alignment=_CENTER)
        return
    if is_total:
        _apply_style(label_cell, font=_TOTAL_FONT, fill=_TOTAL_FILL)

    _write_val(ws, row, 2, fn(scheme), kind, is_total)
    for i in range(N_CATEGORIES):
        _write_val(ws, row, 3 + i, fn(cats.get(i + 1, {})), kind, is_total)


def _write_val(ws, row: int, col: int, val, kind: str, is_total: bool) -> None:
    if val is None:
        return

    cell = ws.cell(row=row, column=col, value=val)
    _apply_style(cell, border=_CELL_BORDER, alignment=_CENTER)

    if   kind == "money": cell.number_format = 'R #,##0;R -#,##0;"-"'
    elif kind == "int":   cell.number_format = '#,##0;-#,##0;"-"'
    elif kind == "age":   cell.number_format = '0.00;-0.00;"-"'
    elif kind == "pct":   cell.number_format = '0%;-0%;"-"'
    elif kind == "ratio": cell.number_format = '0.00000000;-0.00000000;"-"'
    elif kind == "pct3":  cell.number_format = '0.000%;-0.000%;"-"'
    elif kind == "rate3": cell.number_format = '0.000;-0.000;"-"'

    if is_total:
        _apply_style(cell, font=_TOTAL_FONT, fill=_TOTAL_FILL)
    elif col == 2:
        _apply_style(cell, font=_COL2_BOLD)


def _apply_column_widths(ws, min_width: float = 10.0, max_width: float = 40.0) -> None:
    for col_cells in ws.iter_cols():
        col_letter = col_cells[0].column_letter
        longest = 0
        for cell in col_cells:
            if cell.value is None:
                continue
            length = len(str(cell.value))
            longest = max(longest, length)
        width = max(min_width, min(longest + 2, max_width))
        ws.column_dimensions[col_letter].width = width
