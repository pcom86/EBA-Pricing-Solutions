from __future__ import annotations

from .grid_extract import GridSheet


def group_grid_by_sections(grid: GridSheet, section_fill: str = "#A6A6A6"):
    groups = []
    current_label = None
    current_rows = []
    for row in grid.rows:
        first_cell = row[0]
        is_section_start = (
            first_cell.bold
            and first_cell.fill == section_fill
            and first_cell.display.strip()
        )
        if is_section_start:
            if current_label is not None:
                groups.append((current_label, current_rows))
            current_label = first_cell.display.strip()
            current_rows = [row]
        else:
            current_rows.append(row)
    if current_label is not None:
        groups.append((current_label, current_rows))
    return groups
