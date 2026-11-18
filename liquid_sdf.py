"""Liquid reveal v11: the same drop physics as liquid.py (the v2 spring Mario loves), drawn
as a smooth-union signed distance field instead of hand-built neck curves.

The screen edge is a liquid reservoir: a slab whose surface sits just beyond the edge.
The drop is smooth-min'd with it, so the bulge, the gooey meniscus and the thread that
stretches and pinches off all come from one blend radius `k`:

  * while the drop hangs on (liquid.Liquid.attached) k = K_ATTACH = 2 * SNAP_GAP, so the
    bridge's waist reaches exactly zero width at the moment the physics snaps it;
  * after the snap k springs down (surface tension), which pulls both teardrop tips back
    and lets the edge surface settle flat;
  * on hide k springs back up while the drop rises, the bridge re-forms and the reservoir
    swallows the drop.

Rendering: the SDF is evaluated per device pixel and turned into coverage with a one-pixel
ramp (normalised by the gradient), which is as crisp as QPainter's path antialiasing.
Dark style paints fill + rim + gloss from it; Glass style hands the same SDF to glass.py.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter, QPainterPath, QTransform

import liquid
from liquid import DROP_R, SNAP_GAP, _lerp

K_ATTACH = 2.0 * SNAP_GAP      # blend radius while the drop hangs on (waist = 0 at the snap)
K_FREQ, K_ZETA = 9.0, 0.55     # how fast the surface tension pulls the tips back
EDGE_LEVEL = -2.5              # reservoir surface, just beyond the screen edge

# the string of liquid: after the hourglass pinches, a thin thread keeps the drop tied to
# the edge, stretches as the drop falls away, then snaps in the middle and both halves
# recoil (into the edge and into the drop). Hide is the mirror, driven by the gap.
THREAD_R = 1.25                # thread radius (px) at the moment the hourglass closes
THREAD_HOLD = 0.046            # s the stretched thread holds before it snaps (show, 1x)
THREAD_RECOIL = 0.020          # s for the snapped halves to retract
THREAD_K = 4.0                 # fillet where the thread meets the edge and the drop
REACH_TIME = 0.024             # hide: s for the two stubs to reach out and join
THICKEN_TIME = 0.045           # hide: s for the joined thread to thicken before the hourglass


class LiquidSDF(liquid.Liquid):
    kind = "liquid_sdf"

    def reset_hidden(self):
        super().reset_hidden()
        self.k, self.vk = K_ATTACH, 0.0
        self.thread_age = None
        self.rise_age = None

    def reset_shown(self):
        super().reset_shown()
        self.k, self.vk = 0.0, 0.0
        self.thread_age = None
        self.rise_age = None

    def _k_target(self) -> float:
        if self.attached:
            return K_ATTACH
        if self.mode == "hide" and self.contracting and self.u < 0.035:
            return K_ATTACH            # rising back: the reservoir reaches out for it
        return 0.0

    def _sub(self, h: float):
        was_attached = self.attached
        super()._sub(h)
        if self.mode == "hide" and self.contracting and self.u < 0.035:
            self.rise_age = 0.0 if self.rise_age is None else self.rise_age + h
        else:
            self.rise_age = None
        if self.mode == "show" and was_attached and not self.attached:
            self.thread_age = 0.0                       # hourglass closed: the thread takes over
        elif self.thread_age is not None:
            self.thread_age += h
            if self.landed and self.thread_age < THREAD_HOLD:
                self.thread_age = THREAD_HOLD           # the impact snaps the string
            if self.thread_age > THREAD_HOLD + THREAD_RECOIL or self.mode != "show":
                self.thread_age = None
        if self.attached and self.mode == "show":
            self.k, self.vk = K_ATTACH, 0.0            # exact: waist hits zero at the snap
        else:
            w = 2 * math.pi * K_FREQ
            self.vk += (w * w * (self._k_target() - self.k) - 2 * K_ZETA * w * self.vk) * h
            self.k += self.vk * h
            if self.k < 0:
                self.k, self.vk = 0.0, 0.0

    @property
    def is_settled(self) -> bool:
        return super().is_settled and self.k < 0.05

    # ---- shape
    def drop(self):
        """(cx, cy, hw, hh) of the drop / pill in the edge frame (no hand-made tip)."""
        cx, cy, hw, hh, _tip = self.body()
        if self.attached:
            hw = hh = (hw + hh) / 2
        return cx, cy, hw, hh

    def threads(self, cy, hh):
        """[(v_top, v_bottom, radius)] vertical capsules (edge frame) forming the string."""
        top = cy - hh                                   # top of the drop
        va, vb = EDGE_LEVEL - 1.0, cy - hh * 0.55       # ends buried in the edge / the drop
        mid = (EDGE_LEVEL + top) / 2
        if self.attached:
            return [(va, vb, THREAD_R)]
        if self.mode == "show":
            a = self.thread_age
            if a is None:
                return []
            if a < THREAD_HOLD:                          # stretching, thinning a little
                return [(va, vb, THREAD_R * (1 - 0.40 * a / THREAD_HOLD))]
            f = min(1.0, (a - THREAD_HOLD) / THREAD_RECOIL)      # snapped: halves retract
            r = THREAD_R * 0.60 * (1 - 0.45 * f)
            return [(va, mid + (va - mid) * f, r), (mid + (top - mid) * f, vb, r)]
        # hide (mirror): the reservoir reaches out for the rising drop
        a = getattr(self, "rise_age", None)
        if a is None:
            return []
        if a < REACH_TIME:                              # stubs reach out from edge and drop
            f = liquid._smooth(0.0, 1.0, a / REACH_TIME)
            r = THREAD_R * 0.60 * (0.55 + 0.45 * f)
            return [(va, va + (mid - va) * f, r), (top + (mid - top) * f, vb, r)]
        f = liquid._smooth(REACH_TIME, REACH_TIME + THICKEN_TIME, a)
        return [(va, vb, THREAD_R * (0.60 + 0.40 * f))]

    def sdf_fn(self, flip_h):
        """(f(X, Y) -> signed distance in logical px, bounding QRectF) in widget coords."""
        cx, cy, hw, hh = self.drop()
        if cy + hh < EDGE_LEVEL + 0.2:
            return None
        r = min(hw, hh)
        # the blend only exists once the drop pokes out of the reservoir, so a fully
        # swallowed drop leaves the edge perfectly flat (no bump when hidden)
        k = self.k * liquid._smooth(0.0, DROP_R, cy + hh - EDGE_LEVEL)
        a, b = max(0.0, hw - r), max(0.0, hh - r)
        thr = [t for t in self.threads(cy, hh) if t[2] > 0.05 and t[1] > t[0] + 0.05]
        reach = k * 0.5 + 2.0 + (cy - hh if thr else 0.0)
        x0, x1 = cx - hw - k - 3, cx + hw + k + 3
        v0, v1 = min(0.0, cy - hh - reach) - 1, cy + hh + 3
        v0 = max(v0, -1.0)
        if flip_h is None:
            rect = QRectF(x0, v0, x1 - x0, v1 - v0)
        else:
            rect = QRectF(x0, flip_h - v1, x1 - x0, v1 - v0)

        def f(X, Y):
            V = Y if flip_h is None else flip_h - Y
            if hh > hw + 0.05:                         # taller than wide: an ellipse (falling drop)
                px, py = (X - cx) / hw, (V - cy) / hh
                k0 = np.sqrt(px * px + py * py)
                k1 = np.sqrt((px / hw) ** 2 + (py / hh) ** 2)
                dd = k0 * (k0 - 1.0) / np.maximum(k1, 1e-6)
            else:
                qx = np.maximum(np.abs(X - cx) - a, 0.0)
                qy = np.maximum(np.abs(V - cy) - b, 0.0)
                dd = np.sqrt(qx * qx + qy * qy) - r
            de = V - EDGE_LEVEL                        # reservoir: inside for V < level
            if k > 1e-3:
                hh_ = np.maximum(k - np.abs(dd - de), 0.0) / k
                d = np.minimum(dd, de) - hh_ * hh_ * k * 0.25
            else:
                d = np.minimum(dd, de)
            for v_a, v_b, tr in thr:                   # the string, filleted into both ends
                dt = np.sqrt((X - cx) ** 2 + (V - np.clip(V, v_a, v_b)) ** 2) - tr
                hh_ = np.maximum(THREAD_K - np.abs(d - dt), 0.0) / THREAD_K
                d = np.minimum(d, dt) - hh_ * hh_ * THREAD_K * 0.25
            return d
        return f, rect

    def body_rect(self, flip_h):
        cx, cy, hw, hh = self.drop()
        vy = (flip_h - cy) if flip_h is not None else cy
        return QRectF(cx - hw, vy - hh, 2 * hw, 2 * hh), min(hw, hh)

    def clip_path(self, flip_h) -> QPainterPath:
        cx, cy, hw, hh = self.drop()
        path = liquid.stadium_path(cx, cy, hw, hh, 0.0)
        if flip_h is not None:
            path = QTransform(1, 0, 0, -1, 0, flip_h).map(path)
        return path


# ================================================================ dark painting
def _grid(rect: QRectF, s: float, ww: float, wh: float):
    x0 = max(0, int(math.floor(rect.left() * s)))
    y0 = max(0, int(math.floor(rect.top() * s)))
    x1 = min(int(round(ww * s)), int(math.ceil(rect.right() * s)))
    y1 = min(int(round(wh * s)), int(math.ceil(rect.bottom() * s)))
    return x0, y0, x1, y1


def coverage(sd: np.ndarray, s: float):
    """sd in logical px -> (signed distance in device px normalised by the gradient)."""
    sdd = sd * s
    gy, gx = np.gradient(sdd)
    g = np.sqrt(gx * gx + gy * gy)
    gn = np.clip(np.where(g > 0.2, g, 1.0), 0.2, 1.0)
    return sdd / gn, g


def paint_dark(p: QPainter, liq: LiquidSDF, flip_h, ww: float, wh: float):
    """Dark style: shadow, black liquid with the pill's rim, and the drop's gloss."""
    res = liq.sdf_fn(flip_h)
    if res is None:
        return None
    f, rect = res
    k_set = liq.settle()
    body, rr = liq.body_rect(flip_h)
    liquid.paint_shadow(p, body, rr, _lerp(0.45, 1.0, k_set))

    s = p.device().devicePixelRatioF() if p.device() is not None else 1.0
    x0, y0, x1, y1 = _grid(rect, s, ww, wh)
    w, h = x1 - x0, y1 - y0
    if w < 2 or h < 2:
        return liq.clip_path(flip_h)
    xs = (np.arange(w, dtype=np.float32) + x0 + 0.5) / s
    ys = (np.arange(h, dtype=np.float32) + y0 + 0.5) / s
    X, Y = np.meshgrid(xs, ys)
    sd, grad = coverage(f(X, Y), s)
    fill_a = np.clip(0.5 - sd, 0.0, 1.0)
    rim_a = np.clip(s * 0.5 + 0.5 - np.abs(sd), 0.0, 1.0)   # a 1 logical px pen on the edge
    rim_a *= np.clip((grad - 0.35) / 0.4, 0.0, 1.0)           # no rim smear across a pinching waist

    fc = liquid._mix(liquid.C_LIQUID, liquid.C_SETTLED, k_set)
    rim = liquid.C_RIM.alpha() / 255.0 * _lerp(0.6, 1.0, k_set)
    fa = fc.alpha() / 255.0
    # source-over: rim (white) over the fill
    a_fill = fill_a * fa
    a_rim = rim_a * rim
    alpha = a_rim + a_fill * (1 - a_rim)
    col = np.empty((h, w, 3), np.float32)
    for i, c in enumerate((fc.blueF(), fc.greenF(), fc.redF())):
        col[..., i] = a_rim * 1.0 + a_fill * c * (1 - a_rim)          # premultiplied

    g = liq.gloss()
    if g > 0.01:                                       # glossy highlight, top-left of the drop
        cx, cy, hw, hh = liq.drop()
        vy = (flip_h - cy) if flip_h is not None else cy
        hx, hy = cx - rr * 0.36, vy - min(hh, rr * 1.3) * 0.46
        ang = math.radians(38)
        dx, dy = X - hx, Y - hy
        u = dx * math.cos(ang) - dy * math.sin(ang)
        v = (dx * math.sin(ang) + dy * math.cos(ang)) * 2.0
        t = np.sqrt(u * u + v * v) / max(rr * 0.42, 1e-3)
        ga = np.interp(t, [0.0, 0.45, 1.0], [120 * g, 46 * g, 0.0]).astype(np.float32) / 255.0
        ga = ga * fill_a
        col = col * (1 - ga[..., None]) + ga[..., None]
        alpha = alpha * (1 - ga) + ga

    out = np.empty((h, w, 4), np.uint8)
    out[..., :3] = np.clip(col * 255.0 + 0.5, 0, 255)
    out[..., 3] = np.clip(alpha * 255.0 + 0.5, 0, 255)
    img = QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)
    p.drawImage(QRectF(x0 / s, y0 / s, w / s, h / s), img)
    return liq.clip_path(flip_h)


def paint_glass(p: QPainter, liq: LiquidSDF, flip_h, renderer, ww, wh, amount: float = 1.0):
    res = liq.sdf_fn(flip_h)
    if res is None:
        return None
    f, rect = res
    body, rr = liq.body_rect(flip_h)
    liquid.paint_shadow(p, body, rr, 0.55 * _lerp(0.5, 1.0, liq.settle()) * amount)
    renderer.paint_sdf(p, f, rect, ww, wh)
    return liq.clip_path(flip_h)
