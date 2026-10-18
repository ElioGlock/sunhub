"""Tema escuro do app (cores do Sunhub) e ícone desenhado em código."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap

BG, SIDE, PANEL, LINE, LINE2 = "#0E1217", "#0B0F14", "#131921", "#1F2733", "#2A3442"
TEXT, MUTED, DIM = "#E6EAF0", "#8A96A6", "#6F7C8C"
SUN, SUN_INK, OK, WARN, ERR = "#F5B83D", "#2A1C00", "#4ADE80", "#F2B84B", "#F87171"
CHECK = (Path(__file__).with_name("check.svg")).as_posix()
MONO = "'IBM Plex Mono','JetBrains Mono','Fira Code','DejaVu Sans Mono','Liberation Mono',monospace"

QSS = f"""
* {{ color: {TEXT}; }}
QMainWindow, QWidget#content, QScrollArea > QWidget > QWidget#page {{ background: {BG}; }}
QWidget#side {{ background: {SIDE}; border-right: 1px solid {LINE}; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[dim="true"] {{ color: {DIM}; }}
QLabel[h1="true"] {{ font-size: 20pt; font-weight: 600; }}
QLabel[h2="true"] {{ font-size: 12.5pt; font-weight: 600; }}
QLabel[mono="true"] {{ font-family: {MONO}; }}
QLabel[ref="true"] {{ font-family: {MONO}; color: {SUN}; }}
QLabel[brand="true"] {{ font-family: {MONO}; font-size: 14pt; font-weight: 500; }}
QLabel[error="true"] {{ color: {ERR}; }}
QLabel[tag="ok"], QLabel[tag="accepted"] {{ background: #12301F; color: {OK}; border-radius: 9px; padding: 1px 8px; }}
QLabel[tag="warn"], QLabel[tag="superseded"], QLabel[tag="deprecated"], QLabel[tag="rejected"] {{
  background: #3A2E12; color: {WARN}; border-radius: 9px; padding: 1px 8px; }}
QLabel[tag="neutral"] {{ background: #1E2733; color: #A9B4C2; border-radius: 9px; padding: 1px 8px; }}
QLabel#flash {{ border-radius: 10px; padding: 10px 14px; border: 1px solid {LINE2}; background: {PANEL}; }}
QLabel#flash[level="ok"] {{ border-color: #1F5132; color: #BFF5D2; }}
QLabel#flash[level="warn"] {{ border-color: #6B5217; color: #F7D488; }}
QLabel#flash[level="error"] {{ border-color: #7A2A2A; color: #FCA5A5; }}
QFrame[card="true"] {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame[sep="true"] {{ background: {LINE}; max-height: 1px; border: none; }}
QPushButton {{ background: transparent; border: 1px solid {LINE2}; border-radius: 8px; padding: 8px 16px; min-height: 22px; }}
QPushButton:hover {{ border-color: #3A4656; background: #161D27; }}
QPushButton:pressed {{ background: #1A2330; }}
QPushButton:disabled {{ color: #5B6675; border-color: {LINE}; }}
QPushButton:focus {{ border-color: {SUN}; }}
QPushButton[primary="true"] {{ background: {SUN}; color: {SUN_INK}; border-color: {SUN}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: #F8C55E; }}
QPushButton[primary="true"]:disabled {{ background: #5E4A1E; border-color: #5E4A1E; color: #2A1C00; }}
QPushButton[ghost="true"] {{ color: {MUTED}; }}
QPushButton[nav="true"] {{ border: none; text-align: left; padding: 10px 12px; color: {MUTED}; font-size: 11pt; }}
QPushButton[nav="true"]:hover {{ color: {TEXT}; background: transparent; }}
QPushButton[nav="true"]:checked {{ background: #1E2733; color: {TEXT}; }}
QPushButton[chip="true"] {{ border-radius: 15px; padding: 5px 14px; color: #A9B4C2; }}
QPushButton[chip="true"]:checked {{ border-color: {SUN}; color: {SUN}; background: #2A2210; }}
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{ background: {BG}; border: 1px solid {LINE2}; border-radius: 8px;
  padding: 8px 10px; selection-background-color: {SUN}; selection-color: {SUN_INK}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {SUN}; }}
QLineEdit[mono="true"], QPlainTextEdit[mono="true"] {{ font-family: {MONO}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE2}; selection-background-color: #1E2733; }}
QPlainTextEdit[log="true"] {{ background: {SIDE}; border: 1px solid {LINE}; border-radius: 12px; font-family: {MONO};
  color: #A9B4C2; padding: 10px; }}
QListWidget {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; outline: none; padding: 0; }}
QListWidget::item {{ border-bottom: 1px solid {LINE}; }}
QListWidget::item:selected, QListWidget::item:hover {{ background: #1A2330; }}
QTextBrowser {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; padding: 14px; }}
QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {LINE2}; background: {BG}; }}
QCheckBox::indicator:hover {{ border-color: {SUN}; }}
QCheckBox::indicator:checked {{ background: {SUN}; border-color: {SUN}; image: url({CHECK}); }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE2}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {LINE2}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: transparent; width: 16px; }}
QStatusBar {{ background: {SIDE}; color: {MUTED}; border-top: 1px solid {LINE}; }}
QStatusBar QLabel {{ color: {MUTED}; }}
QProgressBar {{ background: {LINE}; border: none; border-radius: 2px; max-height: 4px; min-width: 120px; }}
QProgressBar::chunk {{ background: {SUN}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE2}; padding: 4px 8px; }}
QMessageBox {{ background: {PANEL}; }}
"""


def palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: BG, QPalette.AlternateBase: PANEL,
        QPalette.Text: TEXT, QPalette.Button: PANEL, QPalette.ButtonText: TEXT, QPalette.Highlight: SUN,
        QPalette.HighlightedText: SUN_INK, QPalette.ToolTipBase: PANEL, QPalette.ToolTipText: TEXT,
        QPalette.PlaceholderText: DIM, QPalette.Link: SUN,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#5B6675"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5B6675"))
    return p


RAYS = [((12, 2.5), (12, 4.7)), ((12, 19.3), (12, 21.5)), ((2.5, 12), (4.7, 12)), ((19.3, 12), (21.5, 12)),
        ((5.3, 5.3), (6.9, 6.9)), ((17.1, 17.1), (18.7, 18.7)), ((5.3, 18.7), (6.9, 17.1)), ((17.1, 6.9), (18.7, 5.3))]


def sun_pixmap(size: int = 64, background: bool = False) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s, off = size / 24, 0.0
    if background:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(BG))
        p.drawRoundedRect(QRectF(size * .03, size * .03, size * .94, size * .94), size * .22, size * .22)
        s, off = size * 0.75 / 24, size * 0.125
    sun = QColor(SUN)
    p.setPen(QPen(sun, 1.8 * s, Qt.SolidLine, Qt.RoundCap))
    fill = QColor(sun)
    fill.setAlphaF(0.3)
    p.setBrush(fill)
    p.drawEllipse(QPointF(off + 12 * s, off + 12 * s), 4.2 * s, 4.2 * s)
    for (x1, y1), (x2, y2) in RAYS:
        p.drawLine(QPointF(off + x1 * s, off + y1 * s), QPointF(off + x2 * s, off + y2 * s))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(sun_pixmap(size, background=True))
    return icon
