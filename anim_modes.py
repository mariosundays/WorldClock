"""Alternative reveal animations (the default is liquid.Liquid).

Each model has the same interface as liquid.Liquid as far as the window is concerned:
reset_hidden / reset_shown / set_mode / step / is_settled / is_hidden / column_alpha /
content_t / u / mode / landed, plus `kind` and `offset()` (px the finished pill is
shifted away from its rest position: negative = towards / behind the screen edge).
The pill itself is drawn by ui.PillWidget exactly as when settled, just shifted.
"""
from __future__ import annotations

import math

ANIM_KINDS = ("liquid", "slide", "none", "liquid_classic")   # classic: debug / A-B only

# ================================================================ slide tuning
SLIDE_FREQ = 3.2          # Hz, show spring
SLIDE_ZETA = 0.55         # under-damped: one overshoot, one small undershoot, settle
SLIDE_CAP = 7.0           # px: overshoot away from the edge is softly capped here (tanh)
SLIDE_HIDE_ACCEL = 2600.0  # px/s^2: hide is an ease-in (constant pull back into the edge)
SLIDE_HIDE_DRAG = 0.0     # 1/s
SLIDE_CLEAR = 4.0         # px of extra travel so the shadow is fully behind the edge


class SlideModel:
    kind = "slide"

    def __init__(self, travel: float):
        """travel = px from rest until the pill (and its shadow) is behind the edge."""
        self.hidden_at = -(travel + SLIDE_CLEAR)
        self.mode = "show"
        self.reset_shown()

    # ---- states
    def reset_hidden(self):
        self.mode = "hide"
        self.o, self.v = self.hidden_at, 0.0

    def reset_shown(self):
        self.mode = "show"
        self.o, self.v = 0.0, 0.0

    def set_mode(self, mode: str):
        self.mode = mode           # velocity is kept, so reversals are smooth

    # ---- queries
    def offset(self) -> float:
        o = self.o
        if o > 0:                  # soft cap keeps the overshoot elegant and in the window
            o = SLIDE_CAP * math.tanh(o / SLIDE_CAP)
        return o

    @property
    def landed(self) -> bool:
        return self.o > self.hidden_at + 0.5

    u = 1.0
    content_t = 1.0

    def column_alpha(self, i) -> float:
        return 1.0

    @property
    def is_hidden(self) -> bool:
        return self.mode == "hide" and self.o <= self.hidden_at + 1e-3

    @property
    def is_settled(self) -> bool:
        return self.mode == "show" and abs(self.o) < 0.15 and abs(self.v) < 2.0

    # ---- simulation
    def step(self, dt: float):
        n = max(1, math.ceil(dt / 0.002))
        h = dt / n
        w = 2 * math.pi * SLIDE_FREQ
        for _ in range(n):
            if self.mode == "show":
                a = -w * w * self.o - 2 * SLIDE_ZETA * w * self.v
            else:
                a = -SLIDE_HIDE_ACCEL - SLIDE_HIDE_DRAG * self.v
            self.v += a * h
            self.o += self.v * h
            if self.o <= self.hidden_at and self.mode == "hide":
                self.o, self.v = self.hidden_at, 0.0
        if self.is_settled:
            self.reset_shown()


class InstantModel:
    """No animation: shown or hidden on the next frame."""
    kind = "none"
    u = 1.0
    content_t = 1.0

    def __init__(self, travel: float = 0.0):
        self.mode = "show"
        self.visible = True

    def reset_hidden(self):
        self.mode, self.visible = "hide", False

    def reset_shown(self):
        self.mode, self.visible = "show", True

    def set_mode(self, mode: str):
        self.mode = mode
        self.visible = mode == "show"

    def offset(self) -> float:
        return 0.0 if self.visible else -1e6

    @property
    def landed(self) -> bool:
        return self.visible

    def column_alpha(self, i) -> float:
        return 1.0

    @property
    def is_hidden(self) -> bool:
        return self.mode == "hide"

    @property
    def is_settled(self) -> bool:
        return self.mode == "show"

    def step(self, dt: float):
        pass
