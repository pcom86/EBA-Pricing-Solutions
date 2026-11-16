from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from reporting.grid_extract import GridSheet
from reporting.grid_sections import group_grid_by_sections
from reporting.category_breakdown import CB_SECTION_TITLE

from .theme import PILL_QSS


def _qcolor_from_hex(hex_str: str | None):
    if not hex_str:
        return None
    try:
        return QtGui.QColor(hex_str)
    except Exception:
        return None


def populate_table_from_grid(table: "QtWidgets.QTableWidget", grid: GridSheet) -> None:
    table.clear()
    table.setRowCount(grid.n_rows)
    table.setColumnCount(grid.n_cols)
    table.horizontalHeader().setVisible(False)
    table.verticalHeader().setVisible(False)

    for r_idx, row in enumerate(grid.rows):
        for c_idx, cell in enumerate(row):
            item = QtWidgets.QTableWidgetItem(cell.display)

            font = item.font()
            font.setBold(cell.bold)
            item.setFont(font)

            bg = _qcolor_from_hex(cell.fill)
            if bg is not None:
                item.setBackground(QtGui.QBrush(bg))

            fg = _qcolor_from_hex("#" + cell.font_color) if cell.font_color != "000000" else None
            if fg is not None:
                item.setForeground(QtGui.QBrush(fg))

            align_map = {
                "left": QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                "center": QtCore.Qt.AlignCenter,
                "right": QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
            }
            item.setTextAlignment(int(align_map.get(cell.align, QtCore.Qt.AlignLeft)))

            table.setItem(r_idx, c_idx, item)

    table.resizeColumnsToContents()
    table.resizeRowsToContents()


_KNOWN_CB_TITLES: dict[str, str] = {
    title: pkey for pkey, title in CB_SECTION_TITLE.items() if pkey != "education"
}

_EDUCATION_PKEYS = ("educate_a_child", "education_guardian")


def _resolve_pkey_for_section_label(label: str) -> str | None:
    return _KNOWN_CB_TITLES.get(label)


def _trim_inactive_category_columns(rows: list, active_cats) -> list:
    if not rows:
        return rows
    n_cols = len(rows[0])
    keep_idx = [0, 1] + [i + 1 for i in sorted(active_cats) if 0 <= i + 1 < n_cols]
    return [[row[i] for i in keep_idx] for row in rows]


_EXT_FC_SECTION_LABEL = "Extended Family Cover"


def filter_cb_groups_to_active(
    groups: list[tuple[str, list]],
    active_products,
    active_categories: dict,
) -> list[tuple[str, list]]:
    active_products_set = set(active_products or ())
    filtered: list[tuple[str, list]] = []

    for label, rows in groups:
        if label == _EXT_FC_SECTION_LABEL:
            if "ext_fc" not in active_products_set:
                continue
            filtered.append((label, rows))
            continue

        pkey = _resolve_pkey_for_section_label(label)
        if pkey is None:
            active_edu_pkey = next((p for p in _EDUCATION_PKEYS if p in active_products_set), None)
            if active_edu_pkey is None:
                continue
            cat_pkey = active_edu_pkey
        else:
            if pkey not in active_products_set:
                continue
            cat_pkey = pkey

        cats_for_product = active_categories.get(cat_pkey)
        if cats_for_product:
            rows = _trim_inactive_category_columns(rows, cats_for_product)
        filtered.append((label, rows))

    return filtered


class ProductPillGridView(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        V = QtWidgets.QVBoxLayout(self)
        V.setContentsMargins(0, 0, 0, 0)

        self.pill_host = QtWidgets.QWidget()
        self.pill_row = QtWidgets.QHBoxLayout(self.pill_host)
        self.pill_row.setContentsMargins(4, 4, 4, 4)
        self.pill_row.setSpacing(6)
        self.pill_group = QtWidgets.QButtonGroup(self)
        self.pill_group.setExclusive(True)
        self.pill_group.buttonClicked.connect(self._on_pill_clicked)

        self.pill_scroll = QtWidgets.QScrollArea()
        self.pill_scroll.setWidgetResizable(True)
        self.pill_scroll.setFixedHeight(48)
        self.pill_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.pill_scroll.setWidget(self.pill_host)
        V.addWidget(self.pill_scroll)

        self.table = QtWidgets.QTableWidget()
        V.addWidget(self.table, 1)

        self._groups: list[tuple[str, list]] = []
        self._grid: GridSheet | None = None

    def set_grid(
        self,
        grid: GridSheet,
        section_fill: str = "#A6A6A6",
        active_products=None,
        active_categories: dict | None = None,
    ) -> None:
        self._grid = grid
        groups = group_grid_by_sections(grid, section_fill=section_fill)
        if active_products is not None and active_categories is not None:
            groups = filter_cb_groups_to_active(groups, active_products, active_categories)
        self._groups = groups

        for b in self.pill_group.buttons():
            self.pill_group.removeButton(b)
            b.deleteLater()
        while self.pill_row.count():
            item = self.pill_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, (label, _rows) in enumerate(self._groups):
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(PILL_QSS)
            btn.setProperty("section_index", i)
            if i == 0:
                btn.setChecked(True)
            self.pill_group.addButton(btn)
            self.pill_row.addWidget(btn)
        self.pill_row.addStretch()

        if self._groups:
            self._show_section(0)
        else:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)

    def _on_pill_clicked(self, btn) -> None:
        idx = btn.property("section_index")
        if idx is not None:
            self._show_section(int(idx))

    def _show_section(self, idx: int) -> None:
        if not (0 <= idx < len(self._groups)):
            return
        _label, rows = self._groups[idx]
        sub_grid = GridSheet(
            title=self._grid.title if self._grid else "",
            n_rows=len(rows),
            n_cols=(len(rows[0]) if rows else (self._grid.n_cols if self._grid else 0)),
            rows=rows,
        )
        populate_table_from_grid(self.table, sub_grid)
