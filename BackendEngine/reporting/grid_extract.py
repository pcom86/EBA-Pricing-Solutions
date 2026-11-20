from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GridCell:
    value: object
    display: str
    bold: bool = False
    font_color: str = "000000"
    fill: str = None
    align: str = "left"
    bordered: bool = False


@dataclass
class GridSheet:
    title: str
    n_rows: int
    n_cols: int
    rows: list = field(default_factory=list)


def _format_value(value, number_format: str) -> str:
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)

    nf = number_format or ""
    if v == 0 and '"-"' in nf:
        return "-"

    if nf.startswith("R #,##0.00"):
        return f"R {v:,.2f}"
    if nf.startswith("R #,##0"):
        return f"R {v:,.0f}"
    if nf.startswith("#,##0.00"):
        return f"{v:,.2f}"
    if nf.startswith("#,##0"):
        return f"{v:,.0f}"
    if "0.000000%" in nf:
        return f"{v*100:.6f}%"
    if "0.0000%" in nf:
        return f"{v*100:.4f}%"
    if "0.000%" in nf:
        return f"{v*100:.3f}%"
    if "0.00%" in nf:
        return f"{v*100:.2f}%"
    if "0%" in nf:
        return f"{v*100:.0f}%"
    if "0.00000000" in nf:
        return f"{v:.8f}"
    if "0.000;" in nf or nf.strip() == "0.000":
        return f"{v:.3f}"
    if "0.0000;" in nf or nf.strip() == "0.0000":
        return f"{v:.4f}"
    if "0.00;" in nf or nf.strip() == "0.00":
        return f"{v:.2f}"
    return str(value)


def sheet_to_grid(ws, title: str | None = None) -> GridSheet:
    max_row = ws.max_row
    max_col = ws.max_column
    rows = []
    for r in range(1, max_row + 1):
        if ws.row_dimensions[r].hidden:
            continue
        row_cells = []
        for c in range(1, max_col + 1):
            cell = ws.cell(row=r, column=c)
            fill_color = None
            if cell.fill and cell.fill.fgColor and cell.fill.fgColor.rgb not in (None, "00000000"):
                rgb = cell.fill.fgColor.rgb
                if isinstance(rgb, str) and len(rgb) == 8:
                    fill_color = "#" + rgb[2:]
            font_color = "000000"
            if cell.font and cell.font.color and isinstance(cell.font.color.rgb, str) and len(cell.font.color.rgb) == 8:
                font_color = cell.font.color.rgb[2:]
            align = "left"
            if cell.alignment and cell.alignment.horizontal:
                align = cell.alignment.horizontal
            bordered = bool(cell.border and cell.border.left and cell.border.left.style)
            row_cells.append(GridCell(
                value=cell.value,
                display=_format_value(cell.value, cell.number_format),
                bold=bool(cell.font and cell.font.bold),
                font_color=font_color,
                fill=fill_color,
                align=align,
                bordered=bordered,
            ))
        rows.append(row_cells)
    return GridSheet(title=title or ws.title, n_rows=len(rows), n_cols=max_col, rows=rows)
