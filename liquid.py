"""Liquid reveal: a black drop drips off the screen edge, lands and spreads into the pill.

Everything is simulated with time-based springs and drawn in paintEvent only; the
window itself never moves or resizes during the animation.

Geometry is built in the *edge frame*: x = widget x, v = distance from the screen edge
into the screen (v = 0 is the edge). For a bottom-docked clock the path is mirrored.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient, QTransform

# ================================================================ tuning
DROP_R = 15.0              # radius of the falling drop (px)
SNAP_GAP = 13.0            # edge -> drop gap at which the neck snaps (show)
REATTACH_GAP = 9.0         # gap at which the neck re-forms (hide)
NECK_MIN = 0.7             # neck half-width just before it snaps (px)

OOZE_ACCEL = 2600.0                   # gravity on the drop while it hangs off the edge (px/s^2)
NECK_DRAG = 6.0                       # viscous drag of the neck while attached (1/s)
FALL_FREQ, FALL_ZETA = 2.6, 0.50      # drop-position spring once free
LAND_ZETA = 0.80                      # position damping once landed
STRETCH_K, STRETCH_MAX = 0.0003, 0.10  # vertical stretch per px/s of fall speed

SQUASH_KICK = 0.025                   # squash velocity per px/s of landing speed
SQUASH_FREQ, SQUASH_ZETA = 6.0, 0.40

SPREAD_FREQ, SPREAD_ZETA = 2.6, 0.48  # circle -> pill (show), under-damped
OVERSHOOT_CAP = 0.07                  # soft limit of width overshoot (fraction of spread)
VOLUME_K = 0.17                       # px of half-height lost per px of half-width over full

TIP_FREQ, TIP_ZETA = 9.0, 0.35        # teardrop tip + edge stub recoil after the snap

CONTENT_STAGGER = 0.030               # s between columns (left -> right)
CONTENT_FADE = 0.17                   # s per column fade
CONTENT_RISE = 4.0                    # px the content rises while fading in
HIDE_CONTENT_SPEED = 2.6              # content fades out this much faster

CONTRACT_FREQ, CONTRACT_ZETA = 3.4, 0.85  # pill -> circle (hide)
SUCK_FREQ, SUCK_ZETA = 2.6, 1.0           # circle -> back into the edge
SUCK_ACCEL = 900.0                       # extra upward pull while re-attached (px/s^2)
NECK_GROW = 0.07                          # s for the re-formed neck to thicken

C_LIQUID = QColor(7, 7, 9, 250)
C_SETTLED = QColor(16, 16, 19, 236)
C_RIM = QColor(255, 255, 255, 34)

CONTENT_END = 3 * CONTENT_STAGGER + CONTENT_FADE
_K = 0.5523  # cubic circle constant


def _clamp(x, a, b):
    return a if x < a else b if x > b else x


def _lerp(a, b, t):
    return a + (b - a) * t


def _smooth(e0, e1, x):
    t = _clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _spring(x, v, target, freq, zeta, dt, extra=0.0):
    w = 2 * math.pi * freq
    a = w * w * (target - x) - 2 * zeta * w * v + extra
    v += a * dt
    x += v * dt
    return x, v


def _mix(c1: QColor, c2: QColor, t: float) -> QColor:
    return QColor(round(_lerp(c1.red(), c2.red(), t)), round(_lerp(c1.green(), c2.green(), t)),
                  round(_lerp(c1.blue(), c2.blue(), t)), round(_lerp(c1.alpha(), c2.alpha(), t)))


# ================================================================ model
class Liquid:
    def __init__(self, cx: float, vc: float, half_w: float, half_h: float):
        self.cx, self.vc, self.HW, self.HH = cx, vc, half_w, half_h
        self.mode = "show"
        self.reset_shown()

    # ---- states
    def reset_hidden(self):
        self.mode = "hide"
        self.y, self.vy = -DROP_R - 2.0, 0.0
        self.u, self.vu = 0.0, 0.0
        self.sq, self.vsq = 0.0, 0.0
        self.tip, self.vtip = 0.0, 0.0
        self.up, self.vup, self.up0, self.up_b = 0.0, 0.0, 1.0, 0.0
        self.attached, self.neck_a = True, 1.0
        self.landed = False
        self.contracting = True
        self.content_on, self.content_t = False, 0.0

    def reset_shown(self):
        self.mode = "show"
        self.y, self.vy = self.vc, 0.0
        self.u, self.vu = 1.0, 0.0
        self.sq, self.vsq = 0.0, 0.0
        self.tip, self.vtip = 0.0, 0.0
        self.up, self.vup, self.up0, self.up_b = 0.0, 0.0, 1.0, 0.0
        self.attached, self.neck_a = False, 1.0
        self.landed = True
        self.contracting = False
        self.content_on, self.content_t = True, CONTENT_END

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.mode = mode
        self.contracting = False

    # ---- queries
    def gap(self) -> float:
        return self.y - DROP_R

    @property
    def is_hidden(self) -> bool:
        return self.mode == "hide" and self.y <= -DROP_R + 0.5 and self.up < 0.3

    @property
    def is_settled(self) -> bool:
        return (self.mode == "show" and self.landed and self.content_t >= CONTENT_END
                and abs(self.u - 1) < 0.004 and abs(self.vu) < 0.05
                and abs(self.y - self.vc) < 0.05 and abs(self.vy) < 0.3
                and abs(self.sq) < 0.002 and abs(self.vsq) < 0.02)

    def column_alpha(self, i: int) -> float:
        return _smooth(0.0, 1.0, (self.content_t - i * CONTENT_STAGGER) / CONTENT_FADE)

    # ---- simulation
    def step(self, dt: float):
        n = max(1, math.ceil(dt / 0.004))
        h = dt / n
        for _ in range(n):
            self._sub(h)
        if self.is_settled:
            self.reset_shown()

    def _sub(self, h: float):
        R = DROP_R
        if self.mode == "show":
            if not self.landed and self.attached:     # viscous drip: slow start, accelerating
                self.vy += (OOZE_ACCEL - NECK_DRAG * self.vy) * h
                self.y += self.vy * h
                if self.y >= self.vc:
                    self.landed = True
                    self.vsq += self.vy * SQUASH_KICK
            elif not self.landed:
                self.y, self.vy = _spring(self.y, self.vy, self.vc, FALL_FREQ, FALL_ZETA, h)
                if self.y >= self.vc and self.vy > 0:
                    self.landed = True
                    self.vsq += self.vy * SQUASH_KICK          # one-off impulse
            else:
                self.y, self.vy = _spring(self.y, self.vy, self.vc, FALL_FREQ, LAND_ZETA, h)
            if self.attached and self.gap() >= SNAP_GAP:
                self._snap()
            self.u, self.vu = _spring(self.u, self.vu, 1.0 if self.landed else 0.0,
                                      SPREAD_FREQ, SPREAD_ZETA, h)
            if self.landed and not self.content_on and (
                    self.u >= 0.985 or (self.u > 0.9 and self.vu <= 0)):
                self.content_on = True
            if self.content_on:
                self.content_t = min(CONTENT_END, self.content_t + h)
            self.neck_a = min(1.0, self.neck_a + h / NECK_GROW)
        else:
            self.content_on = False
            self.content_t = max(0.0, self.content_t - h * HIDE_CONTENT_SPEED)
            if self.content_t <= CONTENT_FADE * 0.5:
                self.contracting = True
            if self.contracting:
                self.u, self.vu = _spring(self.u, self.vu, 0.0, CONTRACT_FREQ, CONTRACT_ZETA, h)
            else:
                self.u, self.vu = _spring(self.u, self.vu, 1.0, SPREAD_FREQ, SPREAD_ZETA, h)
            if self.contracting and self.u < 0.035:
                self.landed = False
                extra = -SUCK_ACCEL if self.attached else -SUCK_ACCEL * 0.35
                self.y, self.vy = _spring(self.y, self.vy, -R - 6, SUCK_FREQ, SUCK_ZETA, h, extra)
                self.y = max(self.y, -R - 2)
            else:
                self.y, self.vy = _spring(self.y, self.vy, self.vc, FALL_FREQ, LAND_ZETA, h)
            if not self.attached and self.gap() < REATTACH_GAP and self.vy < 0:
                self.attached, self.neck_a = True, 0.0
            if self.attached:
                self.neck_a = min(1.0, self.neck_a + h / NECK_GROW)

        self.sq, self.vsq = _spring(self.sq, self.vsq, 0.0, SQUASH_FREQ, SQUASH_ZETA, h)
        self.tip, self.vtip = _spring(self.tip, self.vtip, 0.0, TIP_FREQ, TIP_ZETA, h)
        if self.tip < -1.5:
            self.tip, self.vtip = -1.5, 0.0
        self.up, self.vup = _spring(self.up, self.vup, 0.0, TIP_FREQ, TIP_ZETA * 1.6, h)
        if self.up < 0:
            self.up, self.vup = 0.0, 0.0

    def _snap(self):
        nk = neck_params(self.cx, self.y, DROP_R, 1.0)
        self.attached = False
        self.up = self.up0 = nk["yw"] + 1.0
        self.up_b = nk["b"] * 0.85
        self.vup = 0.0
        self.tip = max(0.0, (self.gap() - nk["yw"]) * 0.75)
        self.vtip = 0.0

    # ---- shape
    def body(self):
        """(cx, cy, hw, hh, tip) of the drop / pill body in the edge frame."""
        R = DROP_R
        u = self.u
        uu = u if u <= 1 else 1 + OVERSHOOT_CAP * math.tanh((u - 1) / OVERSHOOT_CAP)
        uc = _clamp(u, 0.0, 1.0)
        hw = R + (self.HW - R) * uu
        hh = R + (self.HH - R) * _smooth(0.0, 0.5, uc)
        hh -= VOLUME_K * _clamp(hw - self.HW, -10.0, 40.0)
        hw += self.sq * R
        hh -= self.sq * R * 0.8
        if not self.landed and not self.attached:
            st = _clamp(self.vy * STRETCH_K, -0.1, STRETCH_MAX)
            hh *= 1 + st
            hw /= math.sqrt(1 + st)
        hw = max(hw, R * 0.6)
        hh = max(hh, R * 0.5)
        return self.cx, self.y, hw, hh, self.tip

    def settle(self) -> float:
        return _smooth(0.55, 1.0, _clamp(self.u, 0.0, 1.0))

    def gloss(self) -> float:
        return 1.0 - _smooth(0.0, 0.35, _clamp(self.u, 0.0, 1.0))


# ================================================================ paths (edge frame)
def neck_params(cx: float, cy: float, r: float, a: float) -> dict:
    g = cy - r
    e = _smooth(0.0, 1.0, g / SNAP_GAP)
    phi = math.radians(_lerp(78.0, 30.0, e))
    if cy - r * math.cos(phi) < 1.0:                 # keep the tangent point on screen
        phi = math.acos(_clamp((cy - 1.0) / r, -1.0, 1.0))
    px, py = cx - r * math.sin(phi), cy - r * math.cos(phi)
    yw = py * _lerp(0.55, 0.45, e)
    w_full = r * math.sin(phi)
    w = _lerp(w_full * 0.92, NECK_MIN, e ** 0.9)
    w = max(0.35, w * a)
    b = _lerp(r * 1.3, r * 0.75, e)
    return dict(phi=phi, px=px, py=py, yw=yw, w=w, b=b)


def attached_path(cx: float, cy: float, r: float, a: float) -> QPainterPath:
    """Drop circle hanging off the edge by a gooey neck, as one closed outline."""
    path = QPainterPath()
    if cy + r < 0.3:
        return path
    n = neck_params(cx, cy, r, a)
    phi, px, py, yw, w, b = n["phi"], n["px"], n["py"], n["yw"], n["w"], n["b"]
    L = max(0.0, py - yw) * 0.55
    cphi, sphi = math.cos(phi), math.sin(phi)
    path.moveTo(cx - b, -3)
    path.lineTo(cx - b, 0)
    path.cubicTo(QPointF(cx - b + (b - w) * 0.6, 0), QPointF(cx - w, yw * 0.5), QPointF(cx - w, yw))
    path.cubicTo(QPointF(cx - w, yw + (py - yw) * 0.5),
                 QPointF(px + L * cphi, py - L * sphi), QPointF(px, py))
    path.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), 90 + math.degrees(phi),
               360 - 2 * math.degrees(phi))
    qx = 2 * cx - px
    path.cubicTo(QPointF(qx - L * cphi, py - L * sphi),
                 QPointF(cx + w, yw + (py - yw) * 0.5), QPointF(cx + w, yw))
    path.cubicTo(QPointF(cx + w, yw * 0.5), QPointF(cx + b - (b - w) * 0.6, 0), QPointF(cx + b, 0))
    path.lineTo(cx + b, -3)
    path.closeSubpath()
    return path


def stub_path(cx: float, length: float, base: float) -> QPainterPath:
    """The bit of neck left on the edge after the snap, recoiling into it."""
    path = QPainterPath()
    if length < 0.3 or base < 0.3:
        return path
    rt = min(base * 0.45, length * 0.5)              # rounded tip
    path.moveTo(cx - base, -3)
    path.lineTo(cx - base, 0)
    path.cubicTo(QPointF(cx - base * 0.35, 0), QPointF(cx - rt, length * 0.55),
                 QPointF(cx - rt * 0.55, length - rt * 0.25))
    path.quadTo(QPointF(cx, length + rt * 0.15), QPointF(cx + rt * 0.55, length - rt * 0.25))
    path.cubicTo(QPointF(cx + rt, length * 0.55), QPointF(cx + base * 0.35, 0), QPointF(cx + base, 0))
    path.lineTo(cx + base, -3)
    path.closeSubpath()
    return path


def stadium_path(cx: float, cy: float, hw: float, hh: float, tip: float = 0.0) -> QPainterPath:
    """Rounded body (radius = min(hw, hh)); an optional teardrop tip points at the edge."""
    if hh > hw:                                       # taller than wide: a real ellipse
        return _ellipse_tip(cx, cy, hw, hh, tip)
    r = min(hw, hh)
    fx, fy = max(0.0, hw - r), max(0.0, hh - r)
    t, btm, lft, rgt = cy - hh, cy + hh, cx - hw, cx + hw
    kr = _K * r
    tip = tip if fx < 0.5 else 0.0
    tp = max(0.0, tip)
    ty = t - tp
    s = 1.0 / (1.0 + tp / max(r, 1e-3) * 2.2)       # shorter handles at the top = pointier
    grow = 1.0 + tp / max(r, 1e-3) * 0.55            # longer side handles = fuller shoulders
    path = QPainterPath()
    path.moveTo(cx, ty)
    if fx > 0:
        path.lineTo(cx + fx, t)
    path.cubicTo(QPointF(cx + fx + kr * s, ty), QPointF(rgt, cy - fy - kr * grow), QPointF(rgt, cy - fy))
    if fy > 0:
        path.lineTo(rgt, cy + fy)
    path.cubicTo(QPointF(rgt, cy + fy + kr), QPointF(cx + fx + kr, btm), QPointF(cx + fx, btm))
    if fx > 0:
        path.lineTo(cx - fx, btm)
    path.cubicTo(QPointF(cx - fx - kr, btm), QPointF(lft, cy + fy + kr), QPointF(lft, cy + fy))
    if fy > 0:
        path.lineTo(lft, cy - fy)
    path.cubicTo(QPointF(lft, cy - fy - kr * grow), QPointF(cx - fx - kr * s, ty), QPointF(cx - fx, ty))
    path.closeSubpath()
    return path


def _ellipse_tip(cx, cy, hw, hh, tip):
    tp = max(0.0, tip)
    ty = cy - hh - tp
    kx, ky = _K * hw, _K * hh
    s = 1.0 / (1.0 + tp / max(hw, 1e-3) * 2.2)
    grow = 1.0 + tp / max(hh, 1e-3) * 0.55
    path = QPainterPath()
    path.moveTo(cx, ty)
    path.cubicTo(QPointF(cx + kx * s, ty), QPointF(cx + hw, cy - ky * grow), QPointF(cx + hw, cy))
    path.cubicTo(QPointF(cx + hw, cy + ky), QPointF(cx + kx, cy + hh), QPointF(cx, cy + hh))
    path.cubicTo(QPointF(cx - kx, cy + hh), QPointF(cx - hw, cy + ky), QPointF(cx - hw, cy))
    path.cubicTo(QPointF(cx - hw, cy - ky * grow), QPointF(cx - kx * s, ty), QPointF(cx, ty))
    path.closeSubpath()
    return path


# ================================================================ painting
def body_geometry(liq: Liquid, flip_h: float | None):
    """Outline of the current shape in widget coords: (path, body QRectF, corner r, settle k),
    or None while fully hidden. flip_h = widget height for a bottom-docked clock."""
    cx, cy, hw, hh, tip = liq.body()
    if cy + max(hh, DROP_R) < 0.3 and liq.up < 0.3:
        return None
    xf = QTransform(1, 0, 0, -1, 0, flip_h) if flip_h is not None else QTransform()
    vy = (flip_h - cy) if flip_h is not None else cy     # body centre in widget y
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    if liq.attached:
        rr = hw = hh = (hw + hh) / 2
        path.addPath(attached_path(cx, cy, rr, liq.neck_a))
    else:
        path.addPath(stadium_path(cx, cy, hw, hh, tip))
        rr = min(hw, hh)
    if liq.up >= 0.3:
        base = liq.up_b * (0.35 + 0.65 * _clamp(liq.up / max(liq.up0, 1e-3), 0, 1))
        path.addPath(stub_path(cx, liq.up, base))
    return xf.map(path), QRectF(cx - hw, vy - hh, 2 * hw, 2 * hh), rr, liq.settle()


def paint_shadow(p: QPainter, body: QRectF, rr: float, strength: float):
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    margin = 10
    for i in range(margin, 0, -1):
        a = (26 * (1 - i / margin) ** 2 + 1) * strength
        p.setBrush(QColor(0, 0, 0, int(a)))
        p.drawRoundedRect(body.adjusted(-i, -i + 2, i, i + 2), rr + i, rr + i)
    p.restore()


def paint_body(p: QPainter, liq: Liquid, flip_h: float | None) -> QPainterPath | None:
    """Draw shadow, liquid body, rim and gloss. flip_h = widget height for a
    bottom-docked clock (edge frame mirrored), None for top."""
    cx, cy, hw, hh, tip = liq.body()
    if cy + max(hh, DROP_R) < 0.3 and liq.up < 0.3:
        return None
    xf = QTransform(1, 0, 0, -1, 0, flip_h) if flip_h is not None else QTransform()
    vy = (flip_h - cy) if flip_h is not None else cy     # body centre in widget y
    k = liq.settle()

    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    if liq.attached:
        rr = hw = hh = (hw + hh) / 2           # live size, so re-attaching never pops
        path.addPath(attached_path(cx, cy, rr, liq.neck_a))
    else:
        path.addPath(stadium_path(cx, cy, hw, hh, tip))
        rr = min(hw, hh)
    if liq.up >= 0.3:
        base = liq.up_b * (0.35 + 0.65 * _clamp(liq.up / max(liq.up0, 1e-3), 0, 1))
        path.addPath(stub_path(cx, liq.up, base))
    path = xf.map(path)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # soft shadow under the body (stronger as it becomes the pill)
    margin = 10
    sk = _lerp(0.45, 1.0, k)
    body = QRectF(cx - hw, vy - hh, 2 * hw, 2 * hh)
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(margin, 0, -1):
        a = (26 * (1 - i / margin) ** 2 + 1) * sk
        p.setBrush(QColor(0, 0, 0, int(a)))
        p.drawRoundedRect(body.adjusted(-i, -i + 2, i, i + 2), rr + i, rr + i)

    p.setBrush(_mix(C_LIQUID, C_SETTLED, k))
    rim = QColor(C_RIM)
    rim.setAlpha(int(C_RIM.alpha() * _lerp(0.6, 1.0, k)))
    p.setPen(QPen(rim, 1))
    p.drawPath(path)

    # glossy highlight, top-left of the drop (screen orientation)
    g = liq.gloss()
    if g > 0.01:
        p.setClipPath(path)
        hx, hy = cx - rr * 0.36, vy - min(hh, rr * 1.3) * 0.46
        p.translate(hx, hy)
        p.rotate(-38)
        grad = QRadialGradient(QPointF(0, 0), rr * 0.42)
        grad.setColorAt(0.0, QColor(255, 255, 255, int(120 * g)))
        grad.setColorAt(0.45, QColor(255, 255, 255, int(46 * g)))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.scale(1.0, 0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(0, 0), rr * 0.42, rr * 0.42)
    p.restore()
    return path
