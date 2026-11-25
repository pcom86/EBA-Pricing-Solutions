from __future__ import annotations

import polars as pl
from PySide6 import QtCore

_NUMERIC = (
    pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.UInt32,
    pl.Int16, pl.UInt16, pl.Int8, pl.UInt8, pl.UInt64,
)


class FastModel(QtCore.QAbstractTableModel):

    def __init__(self, df: pl.DataFrame | None = None):
        super().__init__()
        self._full = df if df is not None else pl.DataFrame()
        self._sort_col = None
        self._desc = False
        self._filter = ""
        self._cols: list[str] = []
        self._rows: list[tuple] = []
        self._num: list[bool] = []
        self._rebuild()

    def set_df(self, df: pl.DataFrame) -> None:
        self._full = df
        self._sort_col = None
        self._filter = ""
        self._rebuild()

    def set_filter(self, text: str) -> None:
        self._filter = text.strip().lower()
        self._rebuild()

    def _rebuild(self) -> None:
        self.beginResetModel()
        df = self._full
        if self._filter and df.width:
            joined = pl.concat_str(
                [pl.col(c).cast(pl.Utf8).fill_null("") for c in df.columns],
                separator="\x1f",
            )
            df = df.filter(joined.str.to_lowercase().str.contains(self._filter, literal=True))
        if self._sort_col is not None:
            df = df.sort(self._sort_col, descending=self._desc, nulls_last=True)
        self._cols = df.columns
        self._rows = df.rows()
        self._num = [dt in _NUMERIC for dt in df.dtypes]
        self.endResetModel()

    def rowCount(self, p=None):
        return 0 if p is not None and p.isValid() else len(self._rows)

    def columnCount(self, p=None):
        return 0 if p is not None and p.isValid() else len(self._cols)

    def data(self, ix, role=QtCore.Qt.DisplayRole):
        if not ix.isValid():
            return None
        if role == QtCore.Qt.DisplayRole:
            v = self._rows[ix.row()][ix.column()]
            if v is None:
                return ""
            if self._num[ix.column()]:
                return f"{v:,.2f}" if isinstance(v, float) else f"{v:,}"
            return str(v)
        if role == QtCore.Qt.TextAlignmentRole and self._num[ix.column()]:
            return int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return None

    def headerData(self, s, o, role=QtCore.Qt.DisplayRole):
        if role != QtCore.Qt.DisplayRole:
            return None
        return self._cols[s] if o == QtCore.Qt.Horizontal else s + 1

    def sort(self, col, order):
        if not self._full.width:
            return
        self._sort_col = self._full.columns[col]
        self._desc = order == QtCore.Qt.DescendingOrder
        self._rebuild()
