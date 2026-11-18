"""Settings dialog: choose 1-8 clocks (city, short label, holiday dimming).

Frameless, dark and rounded like the pill. Every change is emitted at once
(`changed(list)`) so the clock updates live; the caller persists it.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QKeyEvent, QPainterPath
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

import clockdata
import ui

FAM = "'Segoe UI Variable Text', 'Segoe UI'"
SH = 14        # shadow margin around the panel
RAD = 16

STYLE = f"""
QWidget {{ color: #e9e9ec; font-family: {FAM}; font-size: 13px; background: transparent; }}
QLabel#title {{ font-family: 'Segoe UI Variable Display', 'Segoe UI'; font-size: 17px; font-weight: 600; }}
QLabel#muted, QLabel#meta {{ color: rgba(255,255,255,120); font-size: 12px; }}
QLabel#city {{ font-size: 13px; color: #f2f2f4; }}
QLabel#hint {{ color: rgba(255,255,255,90); font-size: 11px; }}
QLineEdit {{ background: rgba(255,255,255,13); border: 1px solid rgba(255,255,255,20);
            border-radius: 8px; padding: 6px 9px; selection-background-color: #4a4a55; }}
QLineEdit:focus {{ border-color: rgba(255,255,255,70); background: rgba(255,255,255,18); }}
QLineEdit#label {{ font-weight: 600; letter-spacing: 1.2px; padding: 5px 2px; color: #ffffff; }}
QPushButton {{ border: none; border-radius: 8px; padding: 7px 14px; background: rgba(255,255,255,16); }}
QPushButton:hover {{ background: rgba(255,255,255,28); }}
QPushButton:disabled {{ color: rgba(255,255,255,50); background: rgba(255,255,255,6); }}
QPushButton#primary {{ background: #f1f1f3; color: #121215; font-weight: 600; }}
QPushButton#primary:hover {{ background: #ffffff; }}
QPushButton#add {{ background: transparent; border: 1px dashed rgba(255,255,255,45);
                  color: rgba(255,255,255,170); padding: 9px; }}
QPushButton#add:hover {{ border-color: rgba(255,255,255,110); color: #ffffff; }}
QPushButton#icon {{ background: transparent; color: rgba(255,255,255,110); padding: 0px;
                   font-size: 12px; border-radius: 6px; }}
QPushButton#icon:hover {{ background: rgba(255,255,255,20); color: #ffffff; }}
QPushButton#icon:disabled {{ color: rgba(255,255,255,28); background: transparent; }}
QFrame#row {{ background: rgba(255,255,255,9); border-radius: 11px; }}
QListWidget {{ background: rgba(255,255,255,6); border: 1px solid rgba(255,255,255,14);
              border-radius: 10px; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 7px 9px; border-radius: 7px; color: #e9e9ec; }}
QListWidget::item:hover {{ background: rgba(255,255,255,14); }}
QListWidget::item:selected {{ background: rgba(255,255,255,26); color: #ffffff; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 1px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,40); border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _local_time(tz: str) -> str:
    t = datetime.now(timezone.utc).astimezone(ZoneInfo(tz))
    return f"{t.hour}:{t.minute:02d}"


class IconButton(QAbstractButton):
    """Painted glyph button (cross / up / down) so it never picks up a colour-font glyph."""

    def __init__(self, kind: str, tip: str, size: QSize, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setToolTip(tip)
        self.setFixedSize(size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        en = self.isEnabled()
        if self._hover and en:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 20))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        a = 28 if not en else (235 if self._hover else 120)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.kind == "x":
            pen = QPen(QColor(255, 255, 255, a), 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            d = 4.5
            p.drawLine(c + QPointF(-d, -d), c + QPointF(d, d))
            p.drawLine(c + QPointF(-d, d), c + QPointF(d, -d))
        else:
            s = -1 if self.kind == "up" else 1
            path = QPainterPath()
            path.moveTo(c + QPointF(-4, -2 * s))
            path.lineTo(c + QPointF(4, -2 * s))
            path.lineTo(c + QPointF(0, 2.5 * s))
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, a))
            p.drawPath(path)
        p.end()


class ResultDelegate(QStyledItemDelegate):
    """City in white, country muted, local time right-aligned."""

    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(opt.rect).adjusted(1, 1, -1, -1)
        if opt.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(r, 7, 7)
        elif opt.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 13))
            p.drawRoundedRect(r, 7, 7)
        name, country, t = idx.data(Qt.ItemDataRole.UserRole + 1)
        f = QFont(opt.font)
        f.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
        f.setPixelSize(13)
        p.setFont(f)
        x = r.left() + 10
        p.setPen(QColor(240, 240, 243))
        p.drawText(QRectF(x, r.top(), r.width(), r.height()), Qt.AlignmentFlag.AlignVCenter, name)
        x += p.fontMetrics().horizontalAdvance(name) + 10
        p.setPen(QColor(255, 255, 255, 115))
        p.drawText(QRectF(x, r.top(), r.right() - x - 60, r.height()), Qt.AlignmentFlag.AlignVCenter, country)
        p.drawText(QRectF(r.left(), r.top(), r.width() - 12, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, t)
        p.restore()

    def sizeHint(self, opt, idx):
        return QSize(0, 33)


class Segmented(QWidget):
    """Small segmented control: rounded track, the current option on a light chip."""
    picked = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str, parent=None):
        super().__init__(parent)
        self.options = options               # [(key, text)]
        self.current = current
        self._hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.f = QFont()
        self.f.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
        self.f.setPixelSize(12)
        self.seg_w = 70
        self.setFixedSize(self.seg_w * len(options) + 6, 30)

    def _seg(self, i) -> QRectF:
        return QRectF(3 + i * self.seg_w, 3, self.seg_w, self.height() - 6)

    def _index_at(self, x) -> int:
        i = int((x - 3) // self.seg_w)
        return i if 0 <= i < len(self.options) else -1

    def set_current(self, key: str):
        self.current = key
        self.update()

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().x())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, _e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._index_at(e.position().x())
        if i >= 0 and self.options[i][0] != self.current:
            self.current = self.options[i][0]
            self.update()
            self.picked.emit(self.current)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.setBrush(QColor(255, 255, 255, 13))
        p.drawRoundedRect(r, 9, 9)
        p.setFont(self.f)
        for i, (key, text) in enumerate(self.options):
            sr = self._seg(i)
            on = key == self.current
            if on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(241, 241, 243))
                p.drawRoundedRect(sr, 7, 7)
            elif i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 16))
                p.drawRoundedRect(sr, 7, 7)
            if on:
                f = QFont(self.f)
                f.setWeight(QFont.Weight.DemiBold)
                p.setFont(f)
                p.setPen(QColor(18, 18, 21))
            else:
                p.setFont(self.f)
                p.setPen(QColor(255, 255, 255, 170 if i == self._hover else 130))
            p.drawText(sr, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


FX_ROWS = [
    ("materialize", "Materialize", "The glass condenses out of the desktop"),
    ("merge", "Merge", "Cities flow together into one pill \u00b7 Replaces the animation"),
    ("pointer", "Pointer light", "Light follows the mouse; clicks ripple"),
]

DELAY_STEPS = [0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0]


SPEED_STEPS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


def fmt_speed(v: float) -> str:
    return f"{v:g}\u00d7"


def fmt_delay(v: float) -> str:
    return "1 min" if v >= 60 else f"{v:g} s"


class StepSlider(QWidget):
    """Thin dark track, light fill, round white handle; snaps to `steps`."""
    moved = Signal(float)

    def __init__(self, steps: list[float], value: float, parent=None):
        super().__init__(parent)
        self.steps = steps
        self.idx = min(range(len(steps)), key=lambda i: abs(steps[i] - value))
        self._drag = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(124, 30)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def value(self) -> float:
        return self.steps[self.idx]

    def _x(self, i) -> float:
        return 8 + (self.width() - 16) * i / (len(self.steps) - 1)

    def _set_from(self, x):
        i = min(range(len(self.steps)), key=lambda k: abs(self._x(k) - x))
        if i != self.idx:
            self.idx = i
            self.update()
            self.moved.emit(self.value())

    def mousePressEvent(self, e):
        self._drag = True
        self._set_from(e.position().x())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._set_from(e.position().x())

    def mouseReleaseEvent(self, _e):
        self._drag = False

    def keyPressEvent(self, e):
        d = {Qt.Key.Key_Left: -1, Qt.Key.Key_Down: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: 1}.get(e.key())
        if d:
            self._set_from(self._x(max(0, min(len(self.steps) - 1, self.idx + d))))
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        x0, x1, xv = self._x(0), self._x(len(self.steps) - 1), self._x(self.idx)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(QRectF(x0, cy - 2, x1 - x0, 4), 2, 2)
        p.setBrush(QColor(241, 241, 243))
        p.drawRoundedRect(QRectF(x0, cy - 2, max(4.0, xv - x0), 4), 2, 2)
        for i in range(len(self.steps)):               # faint step ticks
            if i > self.idx:
                p.setBrush(QColor(255, 255, 255, 45))
                p.drawEllipse(QPointF(self._x(i), cy), 1.3, 1.3)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(QPointF(xv, cy + 1), 8, 8)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QPointF(xv, cy), 7, 7)
        p.end()


class Switch(QAbstractButton):
    """Small pill toggle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 17)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        on = self.isChecked()
        if not self.isEnabled():
            p.setOpacity(0.35)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(242, 242, 244) if on else QColor(255, 255, 255, 38))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.right() - 2 - d if on else r.left() + 2
        p.setBrush(QColor(18, 18, 21) if on else QColor(255, 255, 255, 200))
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))
        p.end()


class ClockRow(QFrame):
    up = Signal()
    down = Signal()
    remove = Signal()
    edited = Signal()

    def __init__(self, cfg: dict, idx: int, count: int, parent=None):
        super().__init__(parent)
        self.setObjectName("row")
        self.cfg = cfg
        self.entry = clockdata.db_entry(cfg["id"]) or {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 7, 10, 7)
        lay.setSpacing(10)

        arrows = QVBoxLayout()
        arrows.setSpacing(0)
        self.b_up = self._icon("up", "Move up", self.up.emit, QSize(22, 17))
        self.b_dn = self._icon("down", "Move down", self.down.emit, QSize(22, 17))
        self.b_up.setEnabled(idx > 0)
        self.b_dn.setEnabled(idx < count - 1)
        arrows.addWidget(self.b_up)
        arrows.addWidget(self.b_dn)
        lay.addLayout(arrows)

        self.label = QLineEdit(cfg["label"])
        self.label.setObjectName("label")
        self.label.setMaxLength(3)
        self.label.setFixedWidth(50)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setToolTip("Short label shown on the clock (2-3 letters)")
        self.label.textEdited.connect(self._label_edited)
        lay.addWidget(self.label)

        info = QVBoxLayout()
        info.setSpacing(1)
        self.city = QLabel(self.entry.get("name", cfg["id"]))
        self.city.setObjectName("city")
        self.meta = QLabel()
        self.meta.setObjectName("meta")
        info.addWidget(self.city)
        info.addWidget(self.meta)
        lay.addLayout(info, 1)

        if self.entry.get("holidays"):
            hol = QHBoxLayout()
            hol.setSpacing(6)
            t = QLabel("Holidays")
            t.setObjectName("hint")
            self.sw = Switch()
            self.sw.setChecked(cfg.get("holidays", True))
            self.sw.setToolTip("Dim this clock on its public holidays")
            self.sw.toggled.connect(self._hol_toggled)
            hol.addWidget(t)
            hol.addWidget(self.sw)
            lay.addLayout(hol)

        self.b_rm = self._icon("x", "Remove", self.remove.emit, QSize(26, 26))
        self.b_rm.setEnabled(count > 1)
        lay.addWidget(self.b_rm)
        self.tick()

    def _icon(self, kind, tip, slot, size):
        b = IconButton(kind, tip, size)
        b.clicked.connect(slot)
        return b

    def tick(self):
        if self.entry:
            self.meta.setText(f"{self.entry['country']}  ·  {_local_time(self.entry['tz'])}")

    def _label_edited(self, text):
        clean = "".join(ch for ch in text.upper() if ch.isalpha())[:3]
        if clean != text:
            pos = self.label.cursorPosition()
            self.label.setText(clean)
            self.label.setCursorPosition(min(pos, len(clean)))
        if clean:
            self.cfg["label"] = clean
            self.edited.emit()

    def _hol_toggled(self, on):
        self.cfg["holidays"] = bool(on)
        self.edited.emit()


class SettingsDialog(QWidget):
    changed = Signal(list)
    anim_changed = Signal(str)
    delay_changed = Signal(float)
    monitors_changed = Signal(str)
    style_changed = Signal(str)
    fx_changed = Signal(str, bool)
    speed_changed = Signal(float)
    closed = Signal()

    def __init__(self, clocks: list[dict], anim: str = "liquid", hide_delay: float = 0.5,
                 monitors: str = "one", parent=None, style: str = "dark", fx: dict | None = None,
                 in_speed: float = 1.0):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("World Clock settings")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(STYLE)
        self.clocks = [dict(c) for c in clocks]
        self._drag = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SH + 22, SH + 18, SH + 22, SH + 20)
        outer.setSpacing(12)

        head = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        title = QLabel("World Clock")
        title.setObjectName("title")
        sub = QLabel("Up to 8 cities, in the order they appear on the clock")
        sub.setObjectName("muted")
        tcol.addWidget(title)
        tcol.addWidget(sub)
        head.addLayout(tcol, 1)
        close = IconButton("x", "Close", QSize(28, 28))
        close.clicked.connect(self.close)
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        outer.addLayout(self.rows_box)

        self.add_btn = QPushButton("+  Add clock")
        self.add_btn.setObjectName("add")
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self.open_search)
        outer.addWidget(self.add_btn)

        self.search_box = QWidget()
        sl = QVBoxLayout(self.search_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search a city or country")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._pick_current)
        self.search.installEventFilter(self)
        self.results = QListWidget()
        self.results.setFixedHeight(7 * 33 + 10)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.setItemDelegate(ResultDelegate(self.results))
        self.results.setMouseTracking(True)
        self.results.itemClicked.connect(self._pick)
        sl.addWidget(self.search)
        sl.addWidget(self.results)
        self.search_box.hide()
        outer.addWidget(self.search_box)

        style_row = QHBoxLayout()
        style_row.setContentsMargins(2, 6, 0, 0)
        sl2 = QVBoxLayout()
        sl2.setSpacing(1)
        st = QLabel("Style")
        st.setObjectName("city")
        sd = QLabel("Glass refracts whatever is on screen under the clock")
        sd.setObjectName("meta")
        sl2.addWidget(st)
        sl2.addWidget(sd)
        style_row.addLayout(sl2, 1)
        self.style_seg = Segmented([("dark", "Dark"), ("glass", "Glass")], style)
        self.style_seg.picked.connect(self.style_changed.emit)
        self.style_seg.picked.connect(self._style_picked)
        style_row.addWidget(self.style_seg, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(style_row)

        # ---- Glass effects (Apple Liquid Glass motion); only active in Glass style
        fx = fx or {}
        self.fx_box = QFrame()
        self.fx_box.setObjectName("row")
        fl = QVBoxLayout(self.fx_box)
        fl.setContentsMargins(14, 10, 12, 10)
        fl.setSpacing(8)
        gh = QLabel("Glass effects")
        gh.setObjectName("meta")
        fl.addWidget(gh)
        self.fx_rows = {}
        for key, name, desc in FX_ROWS:
            row = QHBoxLayout()
            row.setSpacing(10)
            col = QVBoxLayout()
            col.setSpacing(0)
            t = QLabel(name)
            t.setObjectName("city")
            d = QLabel(desc)
            d.setObjectName("meta")
            col.addWidget(t)
            col.addWidget(d)
            row.addLayout(col, 1)
            sw = Switch()
            sw.setChecked(bool(fx.get(key, False)))
            sw.setToolTip(desc)
            sw.toggled.connect(lambda on, k=key: self.fx_changed.emit(k, on))
            row.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)
            fl.addLayout(row)
            self.fx_rows[key] = (t, d, sw, desc)
        outer.addWidget(self.fx_box)
        self._style_picked(style)

        anim_row = QHBoxLayout()
        anim_row.setContentsMargins(2, 2, 0, 0)
        al = QVBoxLayout()
        al.setSpacing(1)
        at = QLabel("Animation")
        at.setObjectName("city")
        ad = QLabel("How the clock appears at the screen edge")
        ad.setObjectName("meta")
        al.addWidget(at)
        al.addWidget(ad)
        anim_row.addLayout(al, 1)
        self.anim_seg = Segmented([("liquid", "Liquid"), ("slide", "Slide"), ("none", "None")], anim)
        self.anim_seg.picked.connect(self.anim_changed.emit)
        anim_row.addWidget(self.anim_seg, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(anim_row)

        speed_row = QHBoxLayout()
        speed_row.setContentsMargins(2, 2, 0, 0)
        spl = QVBoxLayout()
        spl.setSpacing(1)
        spt = QLabel("In speed")
        spt.setObjectName("city")
        spd = QLabel("How fast the clock appears")
        spd.setObjectName("meta")
        spl.addWidget(spt)
        spl.addWidget(spd)
        speed_row.addLayout(spl, 1)
        self.speed = StepSlider(SPEED_STEPS, in_speed)
        self.speed_val = QLabel(fmt_speed(self.speed.value()))
        self.speed_val.setObjectName("city")
        self.speed_val.setFixedWidth(38)
        self.speed_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.speed.moved.connect(lambda v: (self.speed_val.setText(fmt_speed(v)),
                                            self.speed_changed.emit(v)))
        speed_row.addWidget(self.speed, 0, Qt.AlignmentFlag.AlignVCenter)
        speed_row.addWidget(self.speed_val, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(speed_row)

        delay_row = QHBoxLayout()
        delay_row.setContentsMargins(2, 2, 0, 0)
        dl = QVBoxLayout()
        dl.setSpacing(1)
        dt = QLabel("Show for")
        dt.setObjectName("city")
        dd = QLabel("How long the clock stays after the mouse leaves")
        dd.setObjectName("meta")
        dl.addWidget(dt)
        dl.addWidget(dd)
        delay_row.addLayout(dl, 1)
        self.delay = StepSlider(DELAY_STEPS, hide_delay)
        self.delay_val = QLabel(fmt_delay(self.delay.value()))
        self.delay_val.setObjectName("city")
        self.delay_val.setFixedWidth(38)
        self.delay_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.delay.moved.connect(lambda v: (self.delay_val.setText(fmt_delay(v)),
                                            self.delay_changed.emit(v)))
        delay_row.addWidget(self.delay, 0, Qt.AlignmentFlag.AlignVCenter)
        delay_row.addWidget(self.delay_val, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(delay_row)

        mon_row = QHBoxLayout()
        mon_row.setContentsMargins(2, 2, 0, 0)
        ml = QVBoxLayout()
        ml.setSpacing(1)
        mt = QLabel("Monitors")
        mt.setObjectName("city")
        md = QLabel("Where the clock can appear")
        md.setObjectName("meta")
        ml.addWidget(mt)
        ml.addWidget(md)
        mon_row.addLayout(ml, 1)
        self.mon_seg = Segmented([("one", "One"), ("all", "All")], monitors)
        self.mon_seg.picked.connect(self.monitors_changed.emit)
        mon_row.addWidget(self.mon_seg, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(mon_row)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,14);")
        outer.addWidget(sep)

        foot = QHBoxLayout()
        hint = QLabel("Changes apply instantly")
        hint.setObjectName("hint")
        foot.addWidget(hint, 1)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.clicked.connect(self.close)
        foot.addWidget(done)
        outer.addSpacing(4)
        outer.addLayout(foot)

        self.setFixedWidth(500 + 2 * SH)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._rebuild()

    def _style_picked(self, style: str):
        """Glass effects only do something in Glass style: grey them out otherwise."""
        glass_on = style == "glass"
        for t, d, sw, desc in self.fx_rows.values():
            sw.setEnabled(glass_on)
            d.setText(desc if glass_on else "Glass style only")
            t.setStyleSheet("" if glass_on else "color: rgba(255,255,255,80);")
            d.setStyleSheet("" if glass_on else "color: rgba(255,255,255,55);")

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)

    # ---- painting / moving
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(SH, SH, -SH, -SH)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(SH, 0, -1):
            p.setBrush(QColor(0, 0, 0, int(34 * (1 - i / SH) ** 2) + 1))
            p.drawRoundedRect(r.adjusted(-i, -i + 4, i, i + 4), RAD + i, RAD + i)
        p.setBrush(QColor(17, 17, 20, 246))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RAD, RAD)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Escape:
            if self.search_box.isVisible():
                self.close_search()
            else:
                self.close()
            return
        super().keyPressEvent(e)

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == e.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                n = self.results.count()
                if n:
                    r = self.results.currentRow() + (1 if e.key() == Qt.Key.Key_Down else -1)
                    self.results.setCurrentRow(max(0, min(n - 1, r)))
                return True
        return super().eventFilter(obj, e)

    # ---- rows
    def _rebuild(self):
        while self.rows_box.count():
            w = self.rows_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        n = len(self.clocks)
        for i, cfg in enumerate(self.clocks):
            row = ClockRow(cfg, i, n)
            row.up.connect(lambda i=i: self._move(i, -1))
            row.down.connect(lambda i=i: self._move(i, 1))
            row.remove.connect(lambda i=i: self._remove(i))
            row.edited.connect(self._emit)
            self.rows_box.addWidget(row)
        full = n >= clockdata.MAX_CLOCKS
        self.add_btn.setEnabled(not full)
        self.add_btn.setText("8 clocks is the maximum" if full else "+  Add clock")
        if full:
            self.close_search()
        self.adjustSize()

    def _tick(self):
        for i in range(self.rows_box.count()):
            w = self.rows_box.itemAt(i).widget()
            if isinstance(w, ClockRow):
                w.tick()

    def _emit(self):
        self.changed.emit([dict(c) for c in self.clocks])

    def _move(self, i, d):
        j = i + d
        if 0 <= j < len(self.clocks):
            self.clocks[i], self.clocks[j] = self.clocks[j], self.clocks[i]
            self._rebuild()
            self._emit()

    def _remove(self, i):
        if len(self.clocks) > 1:
            del self.clocks[i]
            self._rebuild()
            self._emit()

    # ---- search
    def open_search(self):
        self.add_btn.hide()
        self.search_box.show()
        self.search.clear()
        self._filter("")
        self.search.setFocus()
        self.adjustSize()

    def close_search(self):
        self.search_box.hide()
        self.add_btn.show()
        self.adjustSize()

    def _filter(self, text):
        q = _fold(text.strip())
        have = {c["id"] for c in self.clocks}
        self.results.clear()
        hits = []
        for e in clockdata.database():
            if e["id"] in have:
                continue
            name, country = _fold(e["name"]), _fold(e["country"])
            if not q:
                score = 3
            elif name.startswith(q):
                score = 0
            elif country.startswith(q) or e["cc"].lower() == q or e["label"].lower() == q:
                score = 1
            elif q in name or q in country:
                score = 2
            else:
                continue
            hits.append((score, e["name"], e))
        hits.sort(key=lambda h: (h[0], h[1]))
        for _, _, e in hits[:60]:
            it = QListWidgetItem(e["name"])
            it.setData(Qt.ItemDataRole.UserRole, e["id"])
            it.setData(Qt.ItemDataRole.UserRole + 1, (e["name"], e["country"], _local_time(e["tz"])))
            it.setSizeHint(QSize(0, 33))
            self.results.addItem(it)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _pick_current(self):
        it = self.results.currentItem()
        if it:
            self._pick(it)

    def _pick(self, item):
        if len(self.clocks) >= clockdata.MAX_CLOCKS:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        e = clockdata.db_entry(cid)
        self.clocks.append({"id": cid, "label": e["label"], "holidays": True})
        self.close_search()
        self._rebuild()
        self._emit()
