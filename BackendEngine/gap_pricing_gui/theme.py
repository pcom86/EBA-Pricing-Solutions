from PySide6 import QtGui

OM = {
    "green": "#009677",
    "fresh": "#50B848",
    "ink": "#16241F",
    "muted": "#5B6B65",
    "panel": "#F4F8F6",
    "border": "#D7E4DF",
    "white": "#FFFFFF",
    "zebra": "#EAF3F0",
}

PILL_QSS = f"""
QPushButton {{ border:1px solid {OM['border']}; border-radius:12px;
  padding:4px 14px; background:{OM['white']}; color:{OM['ink']}; outline:none; }}
QPushButton:checked {{ background:{OM['green']}; color:{OM['white']}; border-color:{OM['green']}; }}
QPushButton:hover {{ background:{OM['zebra']}; color:{OM['ink']}; border-color:{OM['green']}; }}
QPushButton:checked:hover {{ background:{OM['green']}; color:{OM['white']}; border-color:{OM['green']}; }}
QPushButton:pressed {{ background:{OM['green']}; color:{OM['white']}; border-color:{OM['green']}; }}
QPushButton:focus {{ outline:none; }}
"""

NAV_QSS = f"""
QListWidget {{ background:{OM['panel']}; border:none; font-size:13px; outline:none; }}
QListWidget::item {{
    padding:12px 16px;
    border-left: 3px solid transparent;
    color:{OM['ink']};
    background:transparent;
    outline:none;
}}
QListWidget::item:hover {{ background:{OM['zebra']}; }}
QListWidget::item:selected {{
    background:transparent;
    border-left: 3px solid {OM['green']};
    color:{OM['green']};
    font-weight:600;
    outline:none;
}}
QListWidget::item:selected:hover {{ background:{OM['zebra']}; }}
QListWidget::item:focus {{ outline:none; border-left: 3px solid {OM['green']}; }}
"""

SUMMARY_TABLE_QSS = f"""
QTableWidget {{
    background: {OM['white']}; gridline-color: {OM['border']}; border: 1px solid {OM['border']};
    font-size: 12px; alternate-background-color: {OM['zebra']};
}}
QHeaderView::section {{
    background: {OM['green']}; color: {OM['white']}; padding: 6px; border: none;
    font-weight: 600; font-size: 11px;
}}
QTableWidget::item:selected {{ background: {OM['fresh']}; color: {OM['white']}; }}
QTableWidget::item:hover {{ background: {OM['zebra']}; color: {OM['ink']}; }}
QTableWidget::item:selected:hover {{ background: {OM['fresh']}; color: {OM['white']}; }}
"""


EXPRESS_COMBO_QSS = """
QComboBox {
    border: none;
    margin: 0px;
    padding: 0px 4px;
    background: #FFFF00;
    color: #16241F;
    font-size: 11px;
}
QComboBox:hover { background: #FFF44D; }
QComboBox:focus { border: 1px solid #009677; }
QComboBox::drop-down {
    border: none;
    width: 16px;
}
QComboBox::down-arrow {
    width: 8px; height: 8px;
}
QComboBox QAbstractItemView {
    border: 1px solid #D7E4DF;
    selection-background-color: #009677;
    selection-color: #FFFFFF;
    background: #FFFFFF;
    outline: none;
}
"""


MARGIN_EDIT_QSS = """
QLineEdit {
    border: none;
    margin: 0px;
    padding: 0px 4px;
    background: #FFFF00;
    color: #16241F;
    font-size: 11px;
}
QLineEdit:hover { background: #FFF44D; }
QLineEdit:focus { border: 1px solid #009677; background: #FFFFFF; }
QLineEdit::placeholder { color: #8A8A6A; }
"""


GLOBAL_QSS = f"""
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {OM['white']}, stop:1 #F0F5F3);
    border: 1px solid {OM['border']};
    border-radius: 8px;
    padding: 7px 16px;
    color: {OM['ink']};
}}
QPushButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {OM['white']}, stop:1 {OM['zebra']});
    border-color: {OM['green']};
}}
QPushButton:pressed {{
    background: {OM['green']};
    color: {OM['white']};
    border-color: {OM['green']};
}}
QPushButton:disabled {{
    color: {OM['muted']};
    background: {OM['panel']};
    border-color: {OM['border']};
}}
QPushButton:focus {{ outline: none; }}

/* Primary action (Price / Run Batch) -- filled green, stands out from
   secondary actions (Browse/Load/Save) on the same action bar. */
QPushButton#primaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #00A886, stop:1 {OM['green']});
    border: 1px solid {OM['green']};
    color: {OM['white']};
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #00BD96, stop:1 #00A886);
    border-color: #00A886;
}}
QPushButton#primaryButton:pressed {{
    background: #00785F;
    border-color: #00785F;
}}
QPushButton#primaryButton:disabled {{
    background: {OM['border']};
    border-color: {OM['border']};
    color: {OM['white']};
}}

QLineEdit, QComboBox, QSpinBox {{
    background: {OM['white']};
    border: 1px solid {OM['border']};
    border-radius: 6px;
    padding: 5px 9px;
    selection-background-color: {OM['green']};
    selection-color: {OM['white']};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {OM['muted']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1.5px solid {OM['green']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {OM['muted']}; background: {OM['panel']}; }}
QLineEdit[readOnly="true"] {{ background: {OM['panel']}; color: {OM['muted']}; }}

QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    border: 1px solid {OM['border']};
    border-radius: 6px;
    selection-background-color: {OM['green']};
    selection-color: {OM['white']};
    background: {OM['white']};
    outline: none;
    padding: 2px;
}}

QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px;
    border: none;
    border-radius: 3px;
    background: {OM['panel']};
    margin: 1px 2px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {OM['zebra']}; }}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{ background: {OM['green']}; }}

QScrollBar:vertical {{ background: {OM['panel']}; width: 12px; margin: 0; border-radius: 6px; }}
QScrollBar::handle:vertical {{ background: {OM['border']}; border-radius: 6px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {OM['muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

QScrollBar:horizontal {{ background: {OM['panel']}; height: 12px; margin: 0; border-radius: 6px; }}
QScrollBar::handle:horizontal {{ background: {OM['border']}; border-radius: 6px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {OM['muted']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

QToolTip {{
    background: {OM['ink']};
    color: {OM['white']};
    border: 1px solid {OM['ink']};
    padding: 4px 8px;
    border-radius: 4px;
}}
"""


def apply_light_theme(app) -> None:
    app.setStyle("Fusion")

    palette = QtGui.QPalette()
    ink = QtGui.QColor(OM["ink"])
    white = QtGui.QColor(OM["white"])
    panel = QtGui.QColor(OM["panel"])
    border = QtGui.QColor(OM["border"])
    green = QtGui.QColor(OM["green"])

    palette.setColor(QtGui.QPalette.Window, panel)
    palette.setColor(QtGui.QPalette.WindowText, ink)
    palette.setColor(QtGui.QPalette.Base, white)
    palette.setColor(QtGui.QPalette.AlternateBase, panel)
    palette.setColor(QtGui.QPalette.ToolTipBase, white)
    palette.setColor(QtGui.QPalette.ToolTipText, ink)
    palette.setColor(QtGui.QPalette.Text, ink)
    palette.setColor(QtGui.QPalette.Button, white)
    palette.setColor(QtGui.QPalette.ButtonText, ink)
    palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#FF0000"))
    palette.setColor(QtGui.QPalette.Link, green)
    palette.setColor(QtGui.QPalette.Highlight, green)
    palette.setColor(QtGui.QPalette.HighlightedText, white)

    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, QtGui.QColor(OM["muted"]))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor(OM["muted"]))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor(OM["muted"]))

    app.setPalette(palette)
    app.setStyleSheet(GLOBAL_QSS)
