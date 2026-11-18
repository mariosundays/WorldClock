"""Painting: the pill, the day/night icons, the holiday balloon and the tray icon."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, QSize
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen,
                           QPixmap, QIcon, QTransform)
from PySide6.QtWidgets import QWidget

import anim_modes
import clockdata
import glass
import glass_fx
import liquid
import liquid_sdf

FONT_FAMILIES = ["Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI"]

PILL_H = 60
PAD_X = 14                 # inner horizontal padding of the pill
COL_W = 83.0               # one city column
MARGIN = 10                # soft-shadow margin around the pill
EDGE_GAP = 6               # px between the screen edge and the pill when shown
# The window stays put during the liquid reveal, so it is sized for the widest
# overshoot plus the shadow; its edge side sits exactly on the screen edge.
def pill_width(n: int) -> int:
    return int(round(2 * PAD_X + max(1, n) * COL_W))


def side_for(pill_w: float) -> int:
    """Transparent room each side of the pill: liquid overshoot + shadow."""
    return MARGIN + int(math.ceil(liquid.OVERSHOOT_CAP * (pill_w / 2 - liquid.DROP_R))) + 6


PILL_W = pill_width(4)     # defaults (4 clocks)
SIDE = side_for(PILL_W)
WIN_W = PILL_W + 2 * SIDE
WIN_H = EDGE_GAP + PILL_H + MARGIN + 12
DIM_OPACITY = 0.38

C_BG = QColor(16, 16, 19, 236)
C_BORDER = QColor(255, 255, 255, 34)
C_SEP = QColor(255, 255, 255, 20)
C_CODE = QColor(255, 255, 255, 120)
C_TIME = QColor(255, 255, 255, 235)
C_SUN = QColor(255, 196, 82)
C_SUN_TW = QColor(240, 140, 90)
C_MOON_LIT = QColor(236, 232, 218)
C_MOON_DARK = QColor(255, 255, 255, 34)
# dark-ink versions (glass over a light backdrop)
C_CODE_D = QColor(10, 10, 14, 150)
C_TIME_D = QColor(8, 8, 12, 238)
C_SEP_D = QColor(0, 0, 0, 30)
C_SUN_D = QColor(226, 140, 18)
C_SUN_TW_D = QColor(214, 100, 52)
C_MOON_LIT_D = QColor(54, 54, 64)
C_MOON_DARK_D = QColor(0, 0, 0, 34)
STYLES = ("dark", "glass")


def mix(c1: QColor, c2: QColor, t: float) -> QColor:
    if t <= 0:
        return QColor(c1)
    if t >= 1:
        return QColor(c2)
    return QColor(round(c1.red() + (c2.red() - c1.red()) * t),
                  round(c1.green() + (c2.green() - c1.green()) * t),
                  round(c1.blue() + (c2.blue() - c1.blue()) * t),
                  round(c1.alpha() + (c2.alpha() - c1.alpha()) * t))


def make_font(px: float, weight=QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(int(round(px)))
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


# ---------------------------------------------------------------- icons
def draw_sun(p: QPainter, c: QPointF, r: float, color: QColor):
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(c, r * 0.48, r * 0.48)
    pen = QPen(color, max(1.2, r * 0.16))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for i in range(8):
        a = i * math.pi / 4
        d = QPointF(math.cos(a), math.sin(a))
        p.drawLine(c + d * (r * 0.72), c + d * (r * 0.98))
    p.restore()


def draw_moon(p: QPainter, c: QPointF, r: float, age: float, southern: bool = False,
              ink: float = 0.0):
    """Moon disc: faint dark side, pale lit side with a correct terminator.

    age: 0 new .. 0.5 full .. 1 new. Waxing is lit on the right as seen from
    the northern hemisphere; mirrored for the southern hemisphere.
    """
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(mix(C_MOON_DARK, C_MOON_DARK_D, ink))
    p.drawEllipse(c, r, r)
    k = math.cos(2 * math.pi * age)            # terminator x-scale
    side = 1.0 if age < 0.5 else -1.0           # +1 lit on right (waxing)
    if southern:
        side = -side
    n = 40
    pts = []
    for i in range(n + 1):                      # limb, top -> bottom
        t = -math.pi / 2 + math.pi * i / n
        pts.append(QPointF(c.x() + side * r * math.cos(t), c.y() + r * math.sin(t)))
    for i in range(n + 1):                      # terminator, bottom -> top
        t = math.pi / 2 - math.pi * i / n
        pts.append(QPointF(c.x() + side * r * k * math.cos(t), c.y() + r * math.sin(t)))
    path = QPainterPath()
    path.moveTo(pts[0])
    for pt in pts[1:]:
        path.lineTo(pt)
    path.closeSubpath()
    p.setBrush(mix(C_MOON_LIT, C_MOON_LIT_D, ink))
    p.drawPath(path)
    p.restore()


def draw_state_icon(p: QPainter, c: QPointF, r: float, st: clockdata.CityState, ink: float = 0.0):
    if st.phase == "day":
        draw_sun(p, c, r, mix(C_SUN, C_SUN_D, ink))
    elif st.phase == "twilight":
        draw_sun(p, c, r * 0.92, mix(C_SUN_TW, C_SUN_TW_D, ink))
    else:
        draw_moon(p, c, r * 0.9, st.moon_age, southern=st.city.lat < 0, ink=ink)


def tray_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(s / 2, s / 2)
        r = s * 0.44
        p.setPen(QPen(QColor(235, 235, 235), max(1.0, s * 0.08)))
        p.setBrush(QColor(22, 22, 26))
        p.drawEllipse(c, r, r)
        pen = QPen(QColor(235, 235, 235), max(1.0, s * 0.09))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(c, c + QPointF(0, -r * 0.62))                 # minute hand at 12
        p.drawLine(c, c + QPointF(r * 0.42, r * 0.0))             # hour hand at 3
        p.end()
        icon.addPixmap(pm)
    return icon


# ---------------------------------------------------------------- pill
class PillWidget(QWidget):
    """The visible clock. Painting + column hit-testing; the window behaviour
    lives in worldclock.ClockWindow (which subclasses this). The body is drawn
    by liquid.paint_body from self.liq, so the same paint covers the reveal."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.states: list[clockdata.CityState] = []
        self.edge = "top"
        self.ncols = 0
        self.liq = None
        self.anim_kind = "liquid"
        self.in_speed = 1.0          # Settings > In speed: show animations run this much faster
        self.style = "dark"
        self.fx = {k: False for k in glass_fx.FX_KEYS}
        self.pointer = glass_fx.Pointer()
        self.glass = glass.GlassRenderer()
        self.set_columns(4)
        self.f_code = make_font(10.5, QFont.Weight.DemiBold, 1.4)
        self.f_time = make_font(21, QFont.Weight.Light)

    def set_columns(self, n: int):
        """Resize for n clocks; the liquid model is rebuilt for the new pill width,
        keeping its shown / hidden state."""
        n = max(1, min(clockdata.MAX_CLOCKS, n))
        if n == self.ncols and self.liq is not None:
            return
        self.ncols = n
        self.pill_w = pill_width(n)
        self.side = side_for(self.pill_w)
        self.setFixedSize(self.pill_w + 2 * self.side, WIN_H)
        self._static = liquid.Liquid(self.width() / 2, EDGE_GAP + PILL_H / 2,
                                     self.pill_w / 2, PILL_H / 2)     # settled look
        self._rebuild_model(force=True)

    # ---- which reveal model: the Animation setting, unless a Glass effect overrides it
    def fx_on(self, key: str) -> bool:
        return self.style == "glass" and self.fx.get(key, False)

    def model_kind(self) -> str:
        return "merge" if self.fx_on("merge") else self.anim_kind

    def make_model(self, kind: str):
        if kind == "merge":
            return glass_fx.MergeModel(self.ncols, self.width() / 2, EDGE_GAP + PILL_H / 2,
                                       self.pill_w, PILL_H, PAD_X, COL_W)
        if kind == "slide":
            return anim_modes.SlideModel(EDGE_GAP + PILL_H + MARGIN + 2)
        if kind == "none":
            return anim_modes.InstantModel()
        cls = liquid.Liquid if kind == "liquid_classic" else liquid_sdf.LiquidSDF
        return cls(self.width() / 2, EDGE_GAP + PILL_H / 2, self.pill_w / 2, PILL_H / 2)

    def step_model(self, liq, dt: float):
        """Advance a reveal model; In speed scales time for showing only (hide unchanged),
        which scales every spring frequency, stagger and fade together."""
        liq.step(dt * self.in_speed if liq.mode == "show" else dt)

    def _rebuild_model(self, force: bool = False):
        """(Re)build the reveal model if its kind changed; it starts in the current
        shown / hidden state. Materialize is a wrapper and is added / removed in place."""
        kind = self.model_kind()
        inner = getattr(self.liq, "inner", self.liq)
        if force or inner is None or getattr(inner, "anim_name", None) != kind:
            shown = self.liq is None or (self.liq.mode == "show" and self.liq.landed)
            inner = self.make_model(kind)
            inner.anim_name = kind
            if not shown:
                inner.reset_hidden()
            self.liq = inner
            if self.fx_on("materialize"):
                self.liq = glass_fx.Materialize(inner, 1.0 if shown else 0.0)
                if not shown:
                    self.liq.reset_hidden()
        else:
            wrapped = isinstance(self.liq, glass_fx.Materialize)
            if self.fx_on("materialize") and not wrapped:
                self.liq = glass_fx.Materialize(inner, 1.0)     # takes effect from the next reveal
            elif not self.fx_on("materialize") and wrapped:
                self.liq = inner
        self.update()

    def set_style(self, style: str):
        self.style = style if style in STYLES else "dark"
        if self.liq is not None:
            self._rebuild_model()
        self.update()

    def set_fx(self, key: str, on: bool):
        self.fx[key] = bool(on)
        if key == "pointer" and not on:
            self.pointer.reset()
        self._rebuild_model()

    @property
    def glass_on(self) -> bool:
        return self.style == "glass" and self.glass.has_backdrop()

    def set_anim(self, kind: str):
        """Switch reveal animation; the new model starts in the current shown/hidden state."""
        kind = kind if kind in anim_modes.ANIM_KINDS else "liquid"
        if kind != self.anim_kind:
            self.anim_kind = kind
            self._rebuild_model()

    # geometry helpers (widget-local)
    def pill_origin(self) -> QPoint:
        oy = EDGE_GAP if self.edge != "bottom" else WIN_H - EDGE_GAP - PILL_H
        return QPoint(self.side, oy)

    def pill_rect(self) -> QRectF:
        o = self.pill_origin()
        return QRectF(o.x(), o.y(), self.pill_w, PILL_H)

    def column_rect(self, i: int) -> QRectF:
        o = self.pill_origin()
        return QRectF(o.x() + PAD_X + i * COL_W, o.y(), COL_W, PILL_H)

    def column_at(self, pos: QPointF) -> int:
        for i in range(self.ncols):
            if self.column_rect(i).contains(pos):
                return i
        return -1

    def paintEvent(self, _ev):
        p = QPainter(self)
        self.paint_all(p)
        p.end()

    def _paint_glass(self, p: QPainter, liq, flip, shift: float = 0.0, scale: float = 1.0):
        """Glass body (shadow + refracted backdrop). Returns the outline (unshifted) or None."""
        geo = liquid.body_geometry(liq, flip)
        if geo is None:
            return None
        path, rect, rr, k = geo
        gs = self.glass.scale
        shift = round(shift * gs) / gs        # whole device px, so the lens field is reused
        capsule = None
        if liq is self._static or (not liq.attached and liq.up < 0.3 and abs(liq.u - 1) < 1e-4
                                   and abs(liq.sq) < 1e-4 and abs(liq.tip) < 1e-3):
            capsule = (rect.center().x(), rect.center().y() + shift,
                       rect.width() / 2 * scale, rect.height() / 2 * scale)
        moved = path.translated(0, shift)
        srect = rect.translated(0, shift)
        if scale != 1.0:                      # scale the shape itself (never the painter:
            c = self.pill_rect().center()     # the refraction must stay registered)
            c.setY(c.y() + shift)
            xf = QTransform().translate(c.x(), c.y()).scale(scale, scale).translate(-c.x(), -c.y())
            moved = xf.map(moved)
            srect = xf.mapRect(srect)
            rr *= scale
        liquid.paint_shadow(p, srect, rr, 0.55 * liquid._lerp(0.5, 1.0, k) * self.glass.amount)
        self.glass.paint(p, moved, self.width(), self.height(), capsule, self.edge)
        return path

    def _paint_merge(self, p: QPainter, flip, scale: float):
        m = getattr(self.liq, "inner", self.liq)
        if m.is_rest_shape:                                 # the finished pill
            self.glass.mask_shadow = False
            return self._paint_glass(p, self._static, flip, 0.0, scale)
        res = m.sdf_fn(flip)
        if res is None:
            return None
        f, rect = res
        self.glass.mask_shadow = True
        self.glass.shadow_k = self.glass.amount
        self.glass.paint_sdf(p, f, rect, self.width(), self.height())
        self.glass.mask_shadow = False
        return self._static_path(flip)

    def _liquid_at_rest(self) -> bool:
        """The SDF liquid is exactly the finished pill: draw it the classic (cached) way."""
        m = self.liq
        return (not m.attached and m.up < 0.3 and abs(m.u - 1) < 1e-4 and abs(m.sq) < 1e-4
                and abs(m.tip) < 1e-3 and m.k < 1e-3 and m.landed)

    def _paint_liquid_sdf(self, p: QPainter, flip, use_glass: bool, scale: float):
        m = getattr(self.liq, "inner", self.liq)
        if not use_glass:
            return liquid_sdf.paint_dark(p, m, flip, self.width(), self.height())
        if scale == 1.0:
            return liquid_sdf.paint_glass(p, m, flip, self.glass, self.width(), self.height(),
                                          self.glass.amount)
        # materialize / squish scale: scale the field about the pill centre (never the painter)
        res = m.sdf_fn(flip)
        if res is None:
            return None
        f, rect = res
        c = self.pill_rect().center()
        cx, cy = c.x(), c.y()

        def fs(X, Y, f=f):
            return f(cx + (X - cx) / scale, cy + (Y - cy) / scale) * scale
        xf = QTransform().translate(cx, cy).scale(scale, scale).translate(-cx, -cy)
        body, rr = m.body_rect(flip)
        liquid.paint_shadow(p, xf.mapRect(body), rr * scale,
                            0.55 * liquid._lerp(0.5, 1.0, m.settle()) * self.glass.amount)
        self.glass.paint_sdf(p, fs, xf.mapRect(rect), self.width(), self.height())
        return m.clip_path(flip)

    def _static_path(self, flip):
        geo = liquid.body_geometry(self._static, flip)
        return geo[0] if geo else None

    def _set_glass_inputs(self):
        """Per-frame effect inputs for the renderer (materialize amount, pointer light)."""
        g = self.glass
        g.amount = getattr(self.liq, "amount", 1.0) if self.fx_on("materialize") else 1.0
        g.light, g.glow, g.ripples, g.spec_k = None, None, [], 1.0
        if not self.fx_on("pointer"):
            return
        pt = self.pointer
        h = glass_fx._smooth(0.0, 1.0, pt.hover)
        if h > 0 and pt.pos is not None:
            c = self.pill_rect().center()
            dx, dy = pt.pos[0] - c.x(), pt.pos[1] - c.y()
            # light comes from the cursor: blend the default top-left with the cursor direction
            ex, ey = dx / (self.pill_w / 2), dy / (PILL_H / 2) - 0.6
            n = math.hypot(ex, ey) or 1.0
            lx0, ly0 = glass.LIGHT
            lx, ly = lx0 + (ex / n - lx0) * h, ly0 + (ey / n - ly0) * h
            n = math.hypot(lx, ly) or 1.0
            g.light = (lx / n, ly / n)
            g.spec_k = 1.0 + 0.35 * h
            g.glow = (pt.pos[0], pt.pos[1], 0.14 * h)
        g.ripples = pt.ripple_params()

    def paint_all(self, p: QPainter):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        flip = self.height() if self.edge == "bottom" else None
        shift = 0.0
        use_glass = self.glass_on
        scale = 1.0
        if use_glass:
            self._set_glass_inputs()
            if self.fx_on("materialize"):
                scale *= getattr(self.liq, "scale", 1.0)
            if self.fx_on("pointer"):
                scale *= self.pointer.scale
        kind = getattr(self.liq, "kind", "liquid")
        if kind == "merge":
            if use_glass:
                body = self._paint_merge(p, flip, scale)
            else:                                 # no backdrop (yet): plain dark pill
                m = getattr(self.liq, "inner", self.liq)
                body = liquid.paint_body(p, self._static, flip) if m.is_rest_shape else None
        elif kind == "liquid_sdf" and not self._liquid_at_rest():
            body = self._paint_liquid_sdf(p, flip, use_glass, scale)
        elif kind in ("liquid", "liquid_sdf"):
            body = self._paint_glass(p, self.liq, flip, 0.0, scale) if use_glass else \
                liquid.paint_body(p, self.liq, flip)
        else:
            off = self.liq.offset()
            if off < -500:                                 # instant mode, hidden
                return
            shift = off if self.edge != "bottom" else -off
            if use_glass:
                body = self._paint_glass(p, self._static, flip, shift, scale)
                p.translate(0, shift)
            else:
                p.translate(0, shift)                      # the finished pill, shifted
                body = liquid.paint_body(p, self._static, flip)
        if self.liq.content_t <= 0 or body is None:
            return
        if scale != 1.0:                                   # content follows the glass scale
            c = self.pill_rect().center()
            p.translate(c)
            p.scale(scale, scale)
            p.translate(-c)
        ink = self.glass.ink if use_glass else 0.0
        c_code = mix(C_CODE, C_CODE_D, ink)
        c_time = mix(C_TIME, C_TIME_D, ink)
        c_sep = mix(C_SEP, C_SEP_D, ink)
        if use_glass:                       # a touch more presence over a busy backdrop
            c_code.setAlpha(min(255, int(c_code.alpha() * 1.35)))
            c_sep.setAlpha(min(255, int(c_sep.alpha() * 1.6)))
        shadow_a = int(70 * (1 - ink)) if use_glass else 0
        p.save()
        if self.liq.u < 0.999:
            p.setClipPath(body)        # content never shows outside the liquid
        pill = self.pill_rect()
        fm_code = QFontMetricsF(self.f_code)
        fm_time = QFontMetricsF(self.f_time)
        n = len(self.states)
        for i, st in enumerate(self.states[:self.ncols]):
            # spread any column count over liquid's 4-slot stagger (same total fade time)
            a = self.liq.column_alpha(i * 3.0 / (n - 1) if n > 1 else 0.0)
            if a <= 0:
                continue
            col = self.column_rect(i)
            p.save()
            p.setOpacity(a * (DIM_OPACITY if st.holiday else 1.0))
            p.translate(0, (1 - a) * liquid.CONTENT_RISE)
            if i > 0:   # separator on this column's left, fades with it
                p.save()
                p.setOpacity(a)
                p.setPen(QPen(c_sep, 1))
                x = round(col.left()) + 0.5
                p.drawLine(QPointF(x, pill.top() + 16), QPointF(x, pill.bottom() - 16))
                p.restore()
            # row 1: icon + code, centred as a group
            icon_r = 6.0
            gap = 5.0
            code_w = fm_code.horizontalAdvance(st.city.code) - self.f_code.letterSpacing()
            group_w = icon_r * 2 + gap + code_w
            gx = col.center().x() - group_w / 2
            row1_y = col.top() + 17.5
            draw_state_icon(p, QPointF(gx + icon_r, row1_y), icon_r, st, ink)
            base = row1_y + (fm_code.capHeight()) / 2
            code_pt = QPointF(gx + icon_r * 2 + gap, base)
            # row 2: time
            tw = fm_time.horizontalAdvance(st.time_text)
            tbase = col.top() + 38.5 + fm_time.capHeight() / 2
            time_pt = QPointF(col.center().x() - tw / 2, tbase)
            if shadow_a > 0:                # soft drop shadow under light text on glass
                p.setPen(QColor(0, 0, 0, shadow_a))
                p.setFont(self.f_code)
                p.drawText(code_pt + QPointF(0, 1), st.city.code)
                p.setFont(self.f_time)
                p.drawText(time_pt + QPointF(0, 1), st.time_text)
            p.setFont(self.f_code)
            p.setPen(c_code)
            p.drawText(code_pt, st.city.code)
            p.setFont(self.f_time)
            p.setPen(c_time)
            p.drawText(time_pt, st.time_text)
            p.restore()
        p.restore()


# ---------------------------------------------------------------- balloon
class Balloon(QWidget):
    """Rounded dark bubble with a small pointer. pointer_up=True: the tail is on
    top (balloon sits under the clock)."""

    TAIL = 7
    PAD_X, PAD_Y = 13, 8
    SH = 6   # shadow margin

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.pointer_up = True
        self.tail_x = 0.0
        self.font_ = make_font(12.5, QFont.Weight.Normal)
        self.font_b = make_font(12.5, QFont.Weight.DemiBold)
        self.head, self.tail_text = "", ""

    def set_content(self, head: str, rest: str, pointer_up: bool):
        self.head, self.tail_text = head, rest
        self.pointer_up = pointer_up
        fmb, fm = QFontMetricsF(self.font_b), QFontMetricsF(self.font_)
        w = fmb.horizontalAdvance(head) + fm.horizontalAdvance(rest) + 2 * self.PAD_X
        h = fm.height() + 2 * self.PAD_Y
        self.setFixedSize(QSize(int(math.ceil(w)) + 2 * self.SH,
                                int(math.ceil(h)) + self.TAIL + 2 * self.SH))
        self.tail_x = self.width() / 2
        self.update()

    def body_rect(self) -> QRectF:
        r = QRectF(self.SH, self.SH, self.width() - 2 * self.SH,
                   self.height() - 2 * self.SH - self.TAIL)
        if self.pointer_up:
            r.translate(0, self.TAIL)
        return r

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = self.body_rect()
        path = QPainterPath()
        path.addRoundedRect(body, 9, 9)
        tx = min(max(self.tail_x, body.left() + 14), body.right() - 14)
        tail = QPainterPath()
        t = self.TAIL
        if self.pointer_up:
            tail.moveTo(tx - t, body.top() + 1)
            tail.lineTo(tx, body.top() - t)
            tail.lineTo(tx + t, body.top() + 1)
        else:
            tail.moveTo(tx - t, body.bottom() - 1)
            tail.lineTo(tx, body.bottom() + t)
            tail.lineTo(tx + t, body.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.SH, 0, -1):
            p.setBrush(QColor(0, 0, 0, int(22 * (1 - i / self.SH) ** 2) + 1))
            p.drawRoundedRect(body.adjusted(-i, -i + 2, i, i + 2), 9 + i, 9 + i)
        p.setBrush(QColor(22, 22, 26, 245))
        p.setPen(QPen(C_BORDER, 1))
        p.drawPath(path)
        fmb = QFontMetricsF(self.font_b)
        base = body.center().y() + fmb.capHeight() / 2
        x = body.left() + self.PAD_X
        p.setFont(self.font_b)
        p.setPen(QColor(255, 255, 255, 235))
        p.drawText(QPointF(x, base), self.head)
        x += fmb.horizontalAdvance(self.head)
        p.setFont(self.font_)
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QPointF(x, base), self.tail_text)
        p.end()


def balloon_text(st: clockdata.CityState) -> tuple[str, str]:
    return f"Bank holiday in {st.city.name}", f"  —  {st.holiday}"
