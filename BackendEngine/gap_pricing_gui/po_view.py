from __future__ import annotations

from reporting.grid_extract import GridSheet
from reporting.grid_sections import group_grid_by_sections

_EDUCATION_PKEYS = ("educate_a_child", "education_guardian")


def _is_product_active(pkey: str, active_products_set: set) -> bool:
    if pkey == "education":
        return any(p in active_products_set for p in _EDUCATION_PKEYS)
    return pkey in active_products_set


def _effective_label_row(row: list) -> list:
    if row[0].display.strip() and not row[1].display.strip():
        return [row[0]] + list(row[2:])
    return list(row[1:])


def _effective_label(row: list):
    if row[0].display.strip() and not row[1].display.strip():
        return row[0]
    return row[1]


def _last_content_col(rows: list) -> int:
    max_idx = -1
    for row in rows:
        for i in range(len(row) - 1, -1, -1):
            if row[i].display.strip():
                if i > max_idx:
                    max_idx = i
                break
    return max_idx


def filter_po_grid_to_active(
    grid: GridSheet,
    po_products: list[str],
    active_products,
    section_fill: str = "#A6A6A6",
) -> GridSheet:
    if not po_products:
        return grid

    active_products_set = set(active_products or ())
    active_flags = [_is_product_active(p, active_products_set) for p in po_products]

    groups = group_grid_by_sections(grid, section_fill=section_fill)
    if not groups:
        return grid

    _block_a_label, block_a_rows = groups[0]
    product_sections = groups[1:]

    if len(product_sections) != len(po_products):
        return grid

    kept_b_rows: list = []
    for (_label, rows), active in zip(product_sections, active_flags):
        if not active:
            continue
        for row in rows:
            kept_b_rows.append(_effective_label_row(row))

    target_width = len(kept_b_rows[0]) if kept_b_rows else (grid.n_cols - 1)

    active_col_indices = [2 + i for i, active in enumerate(active_flags) if active]

    new_block_a_rows: list = []
    for row in block_a_rows:
        label_cell = _effective_label(row)
        kept = [label_cell] + [row[c] for c in active_col_indices]
        pad_needed = target_width - len(kept)
        if pad_needed > 0:
            filler_cell = row[-1]
            kept = kept + [filler_cell] * pad_needed
        elif pad_needed < 0:
            kept = kept[:target_width]
        new_block_a_rows.append(kept)

    all_rows = new_block_a_rows + kept_b_rows

    last_col = _last_content_col(all_rows)
    final_width = last_col + 1 if last_col >= 0 else target_width
    trimmed_rows = [row[:final_width] for row in all_rows]

    return GridSheet(
        title=grid.title,
        n_rows=len(trimmed_rows),
        n_cols=final_width,
        rows=trimmed_rows,
    )


_EXPRESS_ELIGIBLE_PKEYS = {
    "gla", "lsdb", "ci", "spouse_gla", "spouse_lsdb", "terminal",
    "accident_death", "accident_disability", "dismemberment", "education",
}


def compute_visible_products(po_products: list, active_products) -> list:
    active_set = set(active_products or ())
    return [p for p in po_products if _is_product_active(p, active_set)]


def find_express_row(table) -> "int | None":
    for r in range(table.rowCount()):
        item = table.item(r, 0)
        if item and item.text().strip() == "Express over GLA Salary":
            return r
    return None

_MARGIN_ROW_ORDER: tuple[str, ...] = (
    "conversion_option", "cover_to_continue", "pup_cost", "cash_for_loved_ones",
    "pro_loading", "profit_margin", "admin", "business_decision",
    "phasing", "reins_profit", "reins_arb", "rate_basis",
)

_PRICED_MARGINS_TITLE = "Priced Margins - % of Net"

_GLA_FAMILY_EDITABLE = frozenset({
    "conversion_option", "cover_to_continue", "pro_loading", "profit_margin", "admin",
    "business_decision", "phasing", "reins_profit", "reins_arb",
})
_GIP_EDITABLE = frozenset({
    "conversion_option", "pro_loading", "cash_for_loved_ones", "profit_margin", "admin",
    "business_decision", "phasing", "reins_profit", "reins_arb",
})
_TIP_EDITABLE = frozenset({
    "pro_loading", "cash_for_loved_ones", "profit_margin", "admin",
    "business_decision", "phasing", "reins_profit", "reins_arb",
})
_FC_EDITABLE = frozenset({
    "pup_cost", "admin", "business_decision", "phasing", "reins_profit", "reins_arb",
})
_EDUCATION_EDITABLE = frozenset({"profit_margin", "admin"})

_PRICED_MARGIN_EDITABLE_FIELDS: dict[str, frozenset] = {
    **{p: _GLA_FAMILY_EDITABLE for p in (
        "gla", "lsdb", "ci", "spouse_gla", "spouse_lsdb", "terminal",
        "accident_death", "accident_disability", "dismemberment",
    )},
    "gip": _GIP_EDITABLE,
    "tip": _TIP_EDITABLE,
    "fc": _FC_EDITABLE,
    "education": _EDUCATION_EDITABLE,
}


_RATE_BASIS_EDITABLE_PKEYS = frozenset(_PRICED_MARGIN_EDITABLE_FIELDS.keys()) | {"gip", "tip"}


def is_priced_margin_editable(pkey: str, field_key: str) -> bool:
    if field_key == "rate_basis":
        return pkey in _RATE_BASIS_EDITABLE_PKEYS
    return field_key in _PRICED_MARGIN_EDITABLE_FIELDS.get(pkey, frozenset())


def find_priced_margins_rows(table) -> "dict[str, int] | None":
    title_row = None
    for r in range(table.rowCount()):
        item = table.item(r, 0)
        if item and item.text().strip() == _PRICED_MARGINS_TITLE:
            title_row = r
            break
    if title_row is None:
        return None

    first_data_row = title_row + 2
    n_labels = len(_MARGIN_ROW_ORDER)
    if first_data_row + n_labels > table.rowCount():
        return None

    return {
        field_key: first_data_row + i
        for i, field_key in enumerate(_MARGIN_ROW_ORDER)
    }

