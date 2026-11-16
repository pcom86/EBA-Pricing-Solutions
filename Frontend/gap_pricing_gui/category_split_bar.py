from __future__ import annotations

from PySide6 import QtCore, QtWidgets

_VALID_MODES_BY_FAMILY = {
    "gla": ["Sum Assured", "Risk Profile"],
    "fc":  ["Sum Assured", "Risk Profile", "Flat Rand per Mem"],
    "gip": ["Sum Assured", "Risk Profile"],
    "tip": ["Sum Assured", "Risk Profile"],
}
_FAMILY_LABEL = {"gla": "GLA", "fc": "FC", "gip": "GIP", "tip": "TIP"}


class CategorySplitBar(QtWidgets.QWidget):
    changed = QtCore.Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        H = QtWidgets.QHBoxLayout(self)
        H.setContentsMargins(4, 4, 4, 4)
        H.setSpacing(10)

        self._combos: dict[str, QtWidgets.QComboBox] = {}
        for family in ("gla", "fc", "gip", "tip"):
            lbl = QtWidgets.QLabel(f"{_FAMILY_LABEL[family]} Split:")
            combo = QtWidgets.QComboBox()
            combo.addItems(_VALID_MODES_BY_FAMILY[family])
            combo.currentTextChanged.connect(self._on_any_changed)
            H.addWidget(lbl)
            H.addWidget(combo)
            self._combos[family] = combo
        H.addStretch()

    def set_values(self, category_split: dict[str, str]) -> None:
        for family, combo in self._combos.items():
            mode = (category_split or {}).get(family, "")
            combo.blockSignals(True)
            idx = combo.findText(mode)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def values(self) -> dict[str, str]:
        return {family: combo.currentText() for family, combo in self._combos.items()}

    def _on_any_changed(self, _text: str) -> None:
        self.changed.emit(self.values())
