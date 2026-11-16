from __future__ import annotations

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.styles.cell_style import StyleArray

_SECTION_FONT      = Font(bold=True, size=12, color="FFFFFF")
_SECTION_FILL      = PatternFill("solid", fgColor="A6A6A6")
_HIGHLIGHT_FONT    = Font(bold=True, color="FFFFFF")
_HIGHLIGHT_FILL    = PatternFill("solid", fgColor="92D050")
_TABLE_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TABLE_HEADER_FILL = PatternFill("solid", fgColor="92D050")
_ITALIC            = Font(italic=True)
_THIN_SIDE   = Side(style="thin", color="000000")
_CELL_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)
_CENTER = Alignment(horizontal="center", vertical="center")

_RATE_COLS = ["Age Band", "Cover", "Risk Premium", "Profit", "Admin",
              "Initial Commission", "Normal Commission", "VAT", "Gross Risk Premium"]
_RATE_KEYS = ["age_band", "cover", "risk_premium", "profit", "admin",
              "initial_commission", "normal_commission", "vat", "gross"]


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


def _write_val(ws, row, col, value, *, font=None, fill=None, number_format=None):
    c = ws.cell(row=row, column=col, value=value)
    _apply_style(c, border=_CELL_BORDER, alignment=_CENTER, font=font, fill=fill)
    if number_format is not None:
        c.number_format = number_format
    return c


def _write_rate_table(ws, row: int, title: str, rows: list[dict]) -> int:
    if not rows:
        return row

    _write_val(ws, row, 1, title, font=_HIGHLIGHT_FONT, fill=_HIGHLIGHT_FILL)
    for col in range(2, 10):
        _write_val(ws, row, col, None, font=_HIGHLIGHT_FONT, fill=_HIGHLIGHT_FILL)
    row += 1

    for col, label in enumerate(_RATE_COLS, start=1):
        _write_val(ws, row, col, label, font=_TABLE_HEADER_FONT, fill=_TABLE_HEADER_FILL)
    row += 1

    for r in rows:
        _write_val(ws, row, 1, r["age_band"])
        _write_val(ws, row, 2, r["cover"], number_format="#,##0")
        for col, key in zip(range(3, 10), _RATE_KEYS[2:]):
            _write_val(ws, row, col, r[key], number_format="#,##0.00")
        row += 1

    return row + 1


def _write_population_row(ws, row: int, cat: int, entry: dict) -> int:
    labels = ["No. of Members", "Annual Salary Bill", "% Male", "Avg. Salary",
              "Parents Selected", "Extended Family Selected"]
    values = [
        entry.get("n_members", 0),
        entry.get("salary_bill", 0.0),
        entry.get("pct_male"),
        entry.get("avg_salary"),
        "Yes" if entry.get("parents_selected") else "No",
        "Yes" if entry.get("extended_family_selected") else "No",
    ]
    for col, label in enumerate(labels, start=1):
        _write_val(ws, row, col, label, font=_ITALIC)
    row += 1
    fmts = ["#,##0", "#,##0.00", "0.00%", "#,##0.00", None, None]
    for col, (val, fmt) in enumerate(zip(values, fmts), start=1):
        _write_val(ws, row, col, val, number_format=fmt)
    return row + 1


def write_ext_fc_section(ws, start_row: int, ext_fc_data: dict) -> int:
    title = ext_fc_data.get("_display_title", "Extended Family Cover")
    row = start_row

    _write_val(ws, row, 1, title, font=_SECTION_FONT, fill=_SECTION_FILL)
    for col in range(2, 27):
        _write_val(ws, row, col, None, font=_SECTION_FONT, fill=_SECTION_FILL)
    row += 1

    _write_val(ws, row, 1, "Assumption", font=_TABLE_HEADER_FONT, fill=_TABLE_HEADER_FILL)
    _write_val(ws, row, 2, "Standard", font=_TABLE_HEADER_FONT, fill=_TABLE_HEADER_FILL)
    _write_val(ws, row, 3, "Priced", font=_TABLE_HEADER_FONT, fill=_TABLE_HEADER_FILL)
    row += 1
    assumption_rows = [
        ("Profit Margin",  ext_fc_data.get("profit_rate_standard", 0.0), ext_fc_data.get("profit_rate_priced", 0.0)),
        ("Admin & Expenses", ext_fc_data.get("admin_rate_standard", 0.0), ext_fc_data.get("admin_rate_priced", 0.0)),
        ("Rate Basis",     ext_fc_data.get("rate_basis_standard", 1.0), ext_fc_data.get("rate_basis_priced", 1.0)),
        ("Commission",     ext_fc_data.get("commission_pct_standard", 0.0), ext_fc_data.get("commission_pct_priced", 0.0)),
        ("VAT on Commission (scheme)", ext_fc_data.get("vat_pct", 0.0), ext_fc_data.get("vat_pct", 0.0)),
    ]
    for label, std, priced in assumption_rows:
        _write_val(ws, row, 1, label)
        _write_val(ws, row, 2, std, number_format="0.00%")
        _write_val(ws, row, 3, priced, number_format="0.00%")
        row += 1
    row += 1

    categories = ext_fc_data.get("categories", {})
    for cat in sorted(categories.keys()):
        entry = categories[cat]
        _write_val(ws, row, 1, f"Category {cat}", font=_HIGHLIGHT_FONT, fill=_HIGHLIGHT_FILL)
        for col in range(2, 10):
            _write_val(ws, row, col, None, font=_HIGHLIGHT_FONT, fill=_HIGHLIGHT_FILL)
        row += 1

        row = _write_population_row(ws, row, cat, entry)

        if entry.get("parents_selected"):
            row = _write_rate_table(ws, row, "Parents - Standard", entry.get("parents_standard", []))
            row = _write_rate_table(ws, row, "Parents - Priced", entry.get("parents_priced", []))
        if entry.get("extended_family_selected"):
            row = _write_rate_table(ws, row, "Extended Family - Standard", entry.get("extended_family_standard", []))
            row = _write_rate_table(ws, row, "Extended Family - Priced", entry.get("extended_family_priced", []))

        row += 1

    return row


def apply_ext_fc_column_widths(ws) -> None:
    widths = {1: 26, 2: 16, 3: 14, 4: 14, 5: 12, 6: 14, 7: 14, 8: 12, 9: 18}
    for col, width in widths.items():
        letter = ws.cell(row=1, column=col).column_letter
        ws.column_dimensions[letter].width = max(
            ws.column_dimensions[letter].width or 0, width
        )
