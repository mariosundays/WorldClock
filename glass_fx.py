"""Glass-only motion effects (Settings > Glass effects), after Apple's Liquid Glass:

  Materialize  - GlassEffectTransition.materialize: the glass condenses out of the desktop
                 (lens, dispersion, frost, rim light and tint ramp 0 -> 1 together, a tiny
                 0.96 -> 1 scale settle, content last). Wraps whichever reveal model is set.
  Merge        - GlassEffectContainer: every city appears as its own glass blob, then the
                 blobs flow together (smooth-min union, bridges between neighbours, spring
                 settle) into the pill; hide splits them and sucks them back into the edge.
                 Replaces the reveal animation.
  Pointer      - .interactive(): rim light and a soft glow follow the cursor; a press squishes
                 the pill and sends a shimmer ripple from the click point.

All models share liquid.Liquid's interface as far as the window is concerned.
"""
from __future__ import annotations

import copy
import math

import numpy as np

import liquid

FX_KEYS = ("materialize", "merge", "pointer")
FX_SETTING = {k: f"glass_{k}" for k in FX_KEYS}


def _clamp(x, a, b):
    return a if x < a else b if x > b else x


def _smooth(e0, e1, x):
    t = _clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _spring(x, v, target, freq, zeta, dt):
    w = 2 * math.pi * freq
    v += (w * w * (target - x) - 2 * zeta * w * v) * dt
    return x + v * dt, v


# ================================================================ materialize
MAT_SHOW = {"liquid": 0.80, "liquid_sdf": 0.80, "slide": 0.55, "none": 0.25, "merge": 0.75}   # s, 0 -> full glass
MAT_HIDE = {"liquid": 0.40, "liquid_sdf": 0.40, "slide": 0.30, "none": 0.25, "merge": 0.40}
MAT_SCALE = 0.04            # starts at 0.96 of full size
CONTENT_FROM = 0.62         # content fades in over the last part of the ramp


class Materialize:
    """Wraps a reveal model and ramps the glass effects in / out on top of it."""

    def __init__(self, inner, mat: float = 1.0):
        self.inner = inner
        self.mat = mat                     # linear 0..1
        self.mode = inner.mode
        self._held_hide = False            # instant model: hide waits for the dissolve

    def __getattr__(self, name):           # everything else is the wrapped model's
        return getattr(self.__dict__["inner"], name)

    def __copy__(self):
        c = Materialize.__new__(Materialize)
        c.__dict__.update(self.__dict__)
        c.inner = copy.copy(self.inner)
        return c

    @property
    def wrapped_kind(self) -> str:
        return getattr(self.inner, "kind", "liquid")

    # ---- effect values
    @property
    def amount(self) -> float:
        # ease-out: the glass is readable early (a falling droplet already lenses)
        return 1.0 - (1.0 - _clamp(self.mat, 0.0, 1.0)) ** 2.4

    @property
    def scale(self) -> float:
        return 1.0 - MAT_SCALE * (1.0 - _smooth(0.0, 1.0, self.mat)) ** 2

    def _cf(self) -> float:
        return _smooth(CONTENT_FROM, 1.0, self.mat)

    @property
    def content_t(self):
        return self.inner.content_t if self._cf() > 0 else 0.0

    def column_alpha(self, i) -> float:
        return self.inner.column_alpha(i) * self._cf()

    # ---- states
    def reset_hidden(self):
        self.inner.reset_hidden()
        self.mode, self.mat, self._held_hide = "hide", 0.0, False

    def reset_shown(self):
        self.inner.reset_shown()
        self.mode, self.mat, self._held_hide = "show", 1.0, False

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "show":
            self._held_hide = False
            self.inner.set_mode("show")
        elif self.wrapped_kind == "none":
            self._held_hide = True         # keep the pill until it has dissolved
        else:
            self.inner.set_mode("hide")

    def step(self, dt: float):
        k = self.wrapped_kind
        if self.mode == "show":
            self.mat = min(1.0, self.mat + dt / MAT_SHOW.get(k, 0.6))
        else:
            self.mat = max(0.0, self.mat - dt / MAT_HIDE.get(k, 0.35))
            if self._held_hide and self.mat <= 0.0:
                self._held_hide = False
                self.inner.set_mode("hide")
        self.inner.step(dt)

    @property
    def is_settled(self) -> bool:
        return self.mode == "show" and self.mat >= 1.0 and self.inner.is_settled

    @property
    def is_hidden(self) -> bool:
        return self.mode == "hide" and self.mat <= 0.0 and not self._held_hide and self.inner.is_hidden


# ================================================================ merge
BLOB_R = 18.0               # radius of a city blob when it first appears
BLOB_A = 4.0                # half-length of its flat part (a small capsule)
APPEAR_FREQ, APPEAR_ZETA = 3.0, 0.62
MERGE_FREQ, MERGE_ZETA = 2.2, 0.50      # the v2 liquid spring feel
MERGE_CAP = 0.06                        # soft cap on the width overshoot
STAGGER = 0.06                         # s between blobs, centre outwards
MERGE_DELAY = 0.20                      # s after a blob appears before it starts to spread
K_MAX = 22.0                            # smooth-union radius while the blobs are apart (px)
SPLIT_FREQ, SPLIT_ZETA = 3.2, 0.85
VANISH_FREQ, VANISH_ZETA = 3.4, 0.95
HIDE_STAGGER = 0.035
HIDE_CONTENT_SPEED = 2.6


class MergeModel:
    kind = "merge"
    u = 1.0

    def __init__(self, n: int, cx: float, vc: float, pill_w: float, pill_h: float,
                 pad_x: float, col_w: float):
        self.n = max(1, n)
        self.cx, self.vc = cx, vc
        self.R = pill_h / 2
        left = cx - pill_w / 2
        right = cx + pill_w / 2
        self.cc, self.cf, self.af = [], [], []
        for i in range(self.n):
            cl = left + pad_x + i * col_w
            cr = cl + col_w
            L = left + self.R if i == 0 else cl
            Rr = right - self.R if i == self.n - 1 else cr
            self.cc.append((cl + cr) / 2)
            self.cf.append((L + Rr) / 2)
            self.af.append(max(0.0, (Rr - L) / 2))
        mid = (self.n - 1) / 2
        rank = sorted(range(self.n), key=lambda i: (abs(i - mid), i))
        self.order = [0] * self.n
        for r, i in enumerate(rank):
            self.order[i] = r // 2 if self.n % 2 == 0 else (r + 1) // 2   # pairs move together
        self.mode = "show"
        self.reset_shown()

    def __copy__(self):
        c = MergeModel.__new__(MergeModel)
        c.__dict__.update(self.__dict__)
        for k in ("g", "vg", "m", "vm"):
            setattr(c, k, list(getattr(self, k)))
        return c

    # ---- states
    def reset_hidden(self):
        self.mode = "hide"
        self.g, self.vg = [0.0] * self.n, [0.0] * self.n
        self.m, self.vm = [0.0] * self.n, [0.0] * self.n
        self.t = 10.0
        self.content_t = 0.0

    def reset_shown(self):
        self.mode = "show"
        self.g, self.vg = [1.0] * self.n, [0.0] * self.n
        self.m, self.vm = [1.0] * self.n, [0.0] * self.n
        self.t = 10.0
        self.content_t = liquid.CONTENT_END

    def set_mode(self, mode: str):
        if mode != self.mode:
            self.mode = mode
            self.t = 0.0

    # ---- queries
    @property
    def landed(self) -> bool:
        return min(self.m) > 0.9

    @property
    def attached(self) -> bool:
        return False

    def column_alpha(self, i) -> float:
        return _smooth(0.0, 1.0, (self.content_t - i * liquid.CONTENT_STAGGER) / liquid.CONTENT_FADE)

    @property
    def is_settled(self) -> bool:
        return (self.mode == "show" and self.content_t >= liquid.CONTENT_END
                and all(abs(x - 1) < 0.003 for x in self.g + self.m)
                and all(abs(v) < 0.03 for v in self.vg + self.vm))

    @property
    def is_hidden(self) -> bool:
        return self.mode == "hide" and max(self.g) <= 0.004 and self.content_t <= 0

    @property
    def is_rest_shape(self) -> bool:
        """Exactly the finished pill (use the static capsule for painting)."""
        return all(x == 1.0 for x in self.g + self.m)

    # ---- simulation
    def step(self, dt: float):
        n = max(1, math.ceil(dt / 0.004))
        h = dt / n
        for _ in range(n):
            self._sub(h)
        if self.is_settled:
            self.reset_shown()

    def _sub(self, h):
        self.t += h
        if self.mode == "show":
            for i in range(self.n):
                t0 = self.order[i] * STAGGER
                if self.t >= t0:
                    self.g[i], self.vg[i] = _spring(self.g[i], self.vg[i], 1.0, APPEAR_FREQ, APPEAR_ZETA, h)
                if self.t >= t0 + MERGE_DELAY:
                    self.m[i], self.vm[i] = _spring(self.m[i], self.vm[i], 1.0, MERGE_FREQ, MERGE_ZETA, h)
            if min(self.m) > 0.96:
                self.content_t = min(liquid.CONTENT_END, self.content_t + h)
        else:
            self.content_t = max(0.0, self.content_t - h * HIDE_CONTENT_SPEED)
            if self.content_t > liquid.CONTENT_FADE * 0.4:
                return
            top = max(self.order) if self.order else 0
            for i in range(self.n):
                t0 = (top - self.order[i]) * HIDE_STAGGER          # outer blobs leave first
                if self.t < t0:
                    continue
                self.m[i], self.vm[i] = _spring(self.m[i], self.vm[i], 0.0, SPLIT_FREQ, SPLIT_ZETA, h)
                if self.m[i] < 0.35:
                    self.g[i], self.vg[i] = _spring(self.g[i], self.vg[i], -0.06, VANISH_FREQ, VANISH_ZETA, h)
                    if self.g[i] < 0:
                        self.g[i], self.vg[i] = 0.0, 0.0

    # ---- shape (edge frame: v = distance from the screen edge)
    def blobs(self):
        """[(x, v, a, r)] stadiums: centre, half flat length, radius."""
        out = []
        R = self.R
        for i in range(self.n):
            g = self.g[i]
            if g <= 0.004:
                continue
            m = self.m[i]
            mm = m if m <= 1 else 1 + MERGE_CAP * math.tanh((m - 1) / MERGE_CAP)
            mc = _clamp(m, 0.0, 1.0)
            x = self.cc[i] + (self.cf[i] - self.cc[i]) * mc
            a = BLOB_A + (self.af[i] - BLOB_A) * mm
            r = BLOB_R + (R - BLOB_R) * _smooth(0.0, 1.0, mc)
            r -= 0.12 * _clamp(a - self.af[i], 0.0, 20.0)          # keep the volume on overshoot
            gc = _clamp(g, 0.0, 1.15)
            r *= min(1.0, 0.25 + 0.75 * gc) if g < 1 else 1.0 + 0.5 * (gc - 1)
            a *= _clamp(g, 0.0, 1.0)
            v = self.vc - (1.0 - _clamp(g, 0.0, 1.0)) * (self.vc + r)   # rises out of / sinks into the edge
            out.append((x, v, a, max(0.5, r)))
        return out

    def k(self) -> float:
        return K_MAX * (1.0 - _smooth(0.70, 1.0, min(self.m)))

    def sdf_fn(self, flip_h):
        """(sdf(X, Y) in widget coords, bounding QRectF) or None when nothing is visible."""
        from PySide6.QtCore import QRectF
        bl = self.blobs()
        if not bl:
            return None
        k = self.k()
        xs0 = min(x - a - r for x, v, a, r in bl) - k - 2
        xs1 = max(x + a + r for x, v, a, r in bl) + k + 2
        vs0 = min(v - r for x, v, a, r in bl) - k - 2
        vs1 = max(v + r for x, v, a, r in bl) + k + 2
        if flip_h is None:
            rect = QRectF(xs0, vs0, xs1 - xs0, vs1 - vs0)
        else:
            rect = QRectF(xs0, flip_h - vs1, xs1 - xs0, vs1 - vs0)

        def f(X, Y):
            V = Y if flip_h is None else flip_h - Y
            d = None
            for x, v, a, r in bl:
                q = np.maximum(np.abs(X - x) - a, 0.0)
                py = V - v
                di = np.sqrt(q * q + py * py) - r
                if d is None:
                    d = di
                elif k > 1e-3:
                    hh = np.maximum(k - np.abs(d - di), 0.0) / k
                    d = np.minimum(d, di) - hh * hh * k * 0.25
                else:
                    d = np.minimum(d, di)
            return d
        return f, rect


# ================================================================ pointer
HOVER_TIME = 0.18           # s to fade the pointer light in / out
SQUISH = 0.03               # scale lost while pressed
SQUISH_FREQ, SQUISH_ZETA = 4.2, 0.32
RIPPLE_LIFE = 0.65          # s
RIPPLE_SPEED = 330.0        # px/s


class Pointer:
    def __init__(self):
        self.pos = None             # widget logical QPointF-like (x, y)
        self.hover = 0.0
        self.hover_target = 0.0
        self.sq, self.vsq, self.sq_target = 0.0, 0.0, 0.0
        self.ripples = []           # [(x, y, age)]

    def step(self, dt: float) -> bool:
        """Advance; True while something is still moving."""
        if self.hover < self.hover_target:
            self.hover = min(self.hover_target, self.hover + dt / HOVER_TIME)
        elif self.hover > self.hover_target:
            self.hover = max(self.hover_target, self.hover - dt / HOVER_TIME)
        n = max(1, math.ceil(dt / 0.004))
        for _ in range(n):
            self.sq, self.vsq = _spring(self.sq, self.vsq, self.sq_target, SQUISH_FREQ, SQUISH_ZETA, dt / n)
        self.ripples = [(x, y, a + dt) for (x, y, a) in self.ripples if a + dt < RIPPLE_LIFE]
        return self.active

    @property
    def active(self) -> bool:
        return (abs(self.hover - self.hover_target) > 1e-3 or bool(self.ripples)
                or abs(self.sq - self.sq_target) > 1e-3 or abs(self.vsq) > 1e-2)

    @property
    def scale(self) -> float:
        return 1.0 - SQUISH * self.sq

    def press(self, x, y):
        self.sq_target = 1.0
        self.ripples.append((x, y, 0.0))
        self.ripples = self.ripples[-3:]

    def release(self):
        self.sq_target = 0.0

    def reset(self):
        self.__init__()

    def ripple_params(self):
        out = []
        for x, y, a in self.ripples:
            t = a / RIPPLE_LIFE
            out.append((x, y, 4.0 + RIPPLE_SPEED * a, 0.9 * (1 - t) ** 2 * _smooth(0.0, 0.08, a)))
        return out
