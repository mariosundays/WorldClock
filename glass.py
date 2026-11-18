"""Liquid Glass look: the pill refracts and lenses whatever is on screen underneath it.

Technique (see docs/liquid_glass_notes.md):
  * signed distance to the shape edge: analytic for the settled capsule, a distance
    transform of the rasterised QPainterPath during the liquid / slide animations;
  * a convex squircle bevel (kube.io): Snell refraction through the bevel gives a
    displacement along the edge normal, strongest at the rim, zero on the flat top.
    Each pixel samples the backdrop *outward*, so the scene just outside the pill is
    pulled into the rim (the wrap-around look of Apple's glass);
  * R/G/B sampled with slightly different displacement (dispersion at the rim),
    a light frost blur + saturation, adaptive tint / contrast backing, a subtle
    inner shadow and a specular rim lit from the top-left (fainter bottom-right).

The window is excluded from screen capture (SetWindowDisplayAffinity
WDA_EXCLUDEFROMCAPTURE), so a GDI BitBlt of the screen under it never contains the
pill itself. BitBlt waits for the compositor (~1 vsync), so it runs in a worker
thread; the GUI thread only polls for new frames.
"""
from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
from ctypes import wintypes as wt

import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

cv2.setNumThreads(1)      # tiny images: worker-thread spin-up costs more CPU than it saves

# ================================================================ tuning (logical px)
BEVEL = 15.0          # width of the curved rim band
REACH = 15.0          # max displacement at the rim (how far outside we sample)
IOR = 1.5
DISPERSION = 0.22     # R samples (1+D) x, B samples (1-D) x the displacement
FROST = 2.2           # gaussian sigma of the backdrop blur
SATURATION = 1.22
MARGIN = int(math.ceil(REACH * (1 + DISPERSION))) + 4   # capture margin around the window

LIGHT = (-0.55, -0.835)          # light direction the rim faces (screen: up-left)
SPEC_RIM = 0.70                  # thin bright rim facing the light
SPEC_FAR = 0.34                  # fainter rim on the opposite side
SPEC_BASE = 0.10                 # all-round faint edge definition
SPEC_GLOW = 0.16                 # softer glow band inside the lit rim
INNER_SHADOW = 0.16

LUM_TO_DARK = 0.60    # backdrop luminance above which content turns dark ...
LUM_TO_LIGHT = 0.50   # ... and below which it turns light again (hysteresis)
INK_TIME = 0.35       # s for the content colour to cross over
LUM_TIME = 0.25       # s smoothing of the measured luminance

CAPTURE_HZ = 30.0

WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ================================================================ lens profile
def _lens_lut(n: int = 512):
    """Displacement (0..1, normalised) as a function of x = inside distance / BEVEL."""
    x = np.linspace(0.0, 1.0, n)
    eps = 1e-4
    h = lambda t: (1 - (1 - np.clip(t, 0, 1)) ** 4) ** 0.25       # convex squircle
    thick = 0.9                                                      # bevel height / width
    slope = (h(x + eps) - h(x - eps)) / (2 * eps) * thick
    alpha = np.arctan(slope)                                         # surface normal tilt
    beta = np.arcsin(np.sin(alpha) / IOR)                            # refracted ray
    travel = 0.35 + h(x) * thick                                     # depth the ray still crosses
    disp = np.tan(alpha - beta) * travel
    disp[-1] = 0.0
    disp /= disp.max()
    return x, disp.astype(np.float32)


_LX, _LD = _lens_lut()


def lens(d: np.ndarray, scale: float) -> np.ndarray:
    """Displacement in device px for inside distances d (device px)."""
    x = np.clip(d / (BEVEL * scale), 0.0, 1.0)
    return np.interp(x, _LX, _LD).astype(np.float32) * (REACH * scale)


# ================================================================ win32 helpers
_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_gdi32 = ctypes.windll.gdi32 if sys.platform == "win32" else None


class _RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wt.DWORD)]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


if _user32 is not None:
    _user32.GetDC.restype = wt.HDC
    _user32.GetDC.argtypes = [wt.HWND]
    _user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    _user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(_RECT)]
    _user32.MonitorFromWindow.restype = wt.HANDLE
    _user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    _user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(_MONITORINFO)]
    _user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
    _user32.GetWindowDisplayAffinity.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    _gdi32.CreateCompatibleDC.restype = wt.HDC
    _gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    _gdi32.CreateDIBSection.restype = wt.HANDLE
    _gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                        ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
    _gdi32.SelectObject.restype = wt.HANDLE
    _gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
    _gdi32.DeleteObject.argtypes = [wt.HANDLE]
    _gdi32.DeleteDC.argtypes = [wt.HDC]
    _gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]


def set_exclude_from_capture(hwnd: int, on: bool) -> bool:
    """True if the window's capture affinity is now what was asked."""
    if _user32 is None or not hwnd:
        return False
    ok = bool(_user32.SetWindowDisplayAffinity(wt.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE))
    cur = wt.DWORD(0)
    _user32.GetWindowDisplayAffinity(wt.HWND(hwnd), ctypes.byref(cur))
    return ok and (cur.value == WDA_EXCLUDEFROMCAPTURE) == on


def window_rect(hwnd: int):
    r = _RECT()
    _user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def monitor_rect(hwnd: int):
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    _user32.GetMonitorInfoW(_user32.MonitorFromWindow(wt.HWND(hwnd), 2), ctypes.byref(mi))
    m = mi.rcMonitor
    return m.left, m.top, m.right, m.bottom


class _Blitter:
    """Screen -> DIB section copier (one per thread; GDI objects aren't shared)."""

    def __init__(self):
        self.sdc = _user32.GetDC(None)
        self.mdc = _gdi32.CreateCompatibleDC(self.sdc)
        self.size = (0, 0)
        self.hbm = None
        self.arr = None

    def _ensure(self, w, h):
        if (w, h) == self.size:
            return
        if self.hbm:
            _gdi32.DeleteObject(self.hbm)
        b = _BIH()
        b.biSize = ctypes.sizeof(_BIH)
        b.biWidth, b.biHeight, b.biPlanes, b.biBitCount = w, -h, 1, 32
        bits = ctypes.c_void_p()
        self.hbm = _gdi32.CreateDIBSection(self.sdc, ctypes.byref(b), 0, ctypes.byref(bits), None, 0)
        _gdi32.SelectObject(self.mdc, self.hbm)
        self.arr = np.ctypeslib.as_array(ctypes.cast(bits, ctypes.POINTER(ctypes.c_uint8)), shape=(h, w, 4))
        self.size = (w, h)

    def grab(self, l, t, r, b) -> np.ndarray:
        w, h = r - l, b - t
        self._ensure(w, h)
        _gdi32.BitBlt(self.mdc, 0, 0, w, h, self.sdc, l, t, 0x00CC0020)   # SRCCOPY
        return self.arr[:, :, :3].copy()                                   # BGR

    def close(self):
        if self.hbm:
            _gdi32.DeleteObject(self.hbm)
        _gdi32.DeleteDC(self.mdc)
        _user32.ReleaseDC(None, self.sdc)


def capture_rect(hwnd: int):
    """(want, clip): the window rect + MARGIN (device px), and that clipped to its monitor."""
    l, t, r, b = window_rect(hwnd)
    m = MARGIN
    want = (l - m, t - m, r + m, b + m)
    ml, mt, mr, mb = monitor_rect(hwnd)
    clip = (max(want[0], ml), max(want[1], mt), min(want[2], mr), min(want[3], mb))
    return want, clip


def pad_to(img: np.ndarray, want, clip) -> np.ndarray:
    """Edge-replicate the clipped grab back out to the wanted rect (off-screen parts)."""
    return cv2.copyMakeBorder(img, clip[1] - want[1], want[3] - clip[3],
                              clip[0] - want[0], want[2] - clip[2], cv2.BORDER_REPLICATE)


def grab_once(hwnd: int):
    """Synchronous grab (GUI thread), used right before the pill is first shown."""
    want, clip = capture_rect(hwnd)
    if clip[2] <= clip[0] or clip[3] <= clip[1]:
        return None
    bl = _Blitter()
    try:
        img = bl.grab(*clip)
    finally:
        bl.close()
    return pad_to(img, want, clip), want


class Grabber(threading.Thread):
    """Background screen grabber for one window. set_target(hwnd) starts / retargets,
    set_target(None) parks it (no work at all while the pill is hidden)."""

    def __init__(self):
        super().__init__(daemon=True)
        self._hwnd = None
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._frame = None          # (seq, bgr, want)
        self._seq = 0
        self._quit = False
        self.start()

    def set_target(self, hwnd):
        self._hwnd = hwnd
        if hwnd:
            self._wake.set()

    def latest(self):
        with self._lock:
            return self._frame

    def stop(self):
        self._quit = True
        self._hwnd = None
        self._wake.set()

    def run(self):
        bl = None
        prev = None
        period = 1.0 / CAPTURE_HZ
        try:
            while not self._quit:
                hwnd = self._hwnd
                if not hwnd:
                    self._wake.wait()
                    self._wake.clear()
                    prev = None
                    continue
                t0 = time.perf_counter()
                if bl is None:
                    bl = _Blitter()
                try:
                    want, clip = capture_rect(hwnd)
                    if clip[2] > clip[0] and clip[3] > clip[1]:
                        img = pad_to(bl.grab(*clip), want, clip)
                        if prev is None or prev[1] != want or not np.array_equal(prev[0], img):
                            prev = (img, want)
                            with self._lock:
                                self._seq += 1
                                self._frame = (self._seq, img, want)
                except Exception:
                    pass
                rest = period - (time.perf_counter() - t0)
                if rest > 0:
                    time.sleep(rest)
        finally:
            if bl is not None:
                bl.close()


# ================================================================ renderer
class GlassRenderer:
    """Turns (backdrop, shape path) into a premultiplied ARGB image of the glass body.

    The backdrop is a BGR array in device px whose top-left sits at `origin` (device px)
    relative to the widget's top-left; it must cover the widget plus MARGIN."""

    def __init__(self):
        self.bg = None
        self.origin = (0, 0)
        self.scale = 1.0
        self._bg_id = 0
        self._blur = None
        self._blur_id = -1
        self._field_key = None
        self._field = None
        self.lum = None              # smoothed backdrop luminance (0..1)
        self.ink = 0.0               # 0 = light content, 1 = dark content
        self.ink_target = 0.0
        self._t = None
        self.backing = 0.0
        # per-frame effect inputs (set by the pill before painting)
        self.amount = 1.0            # materialize: 0 = plain desktop .. 1 = full glass
        self.spec_k = 1.0            # extra specular gain (pointer hover)
        self.light = None            # (lx, ly) rim light direction, None = top-left default
        self.glow = None             # (x, y, strength) pointer glow, logical px
        self.ripples = []            # [(x, y, radius, strength)] click shimmer rings
        self.mask_shadow = False     # draw the drop shadow from the mask (merge blobs)
        self.shadow_k = 1.0
        self._cache = None
        self._dcache = None

    # ---- inputs
    def set_backdrop(self, bgr: np.ndarray, origin, scale: float = 1.0):
        self.bg = bgr
        self.origin = origin
        self.scale = scale
        self._bg_id += 1

    def has_backdrop(self) -> bool:
        return self.bg is not None

    def reset_adapt(self):
        self.lum = None
        self._t = None

    # ---- field
    def _mask(self, path: QPainterPath, x0, y0, w, h, s) -> np.ndarray:
        img = QImage(w, h, QImage.Format.Format_Grayscale8)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(-x0, -y0)
        p.scale(s, s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawPath(path)
        p.end()
        a = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())[:, :w]
        return a.astype(np.float32) * (1.0 / 255.0)

    def _field_from_mask(self, cov: np.ndarray, s: float, cut):
        """Inside distance + outward normal from a coverage mask via a distance transform.
        cut = (top, bottom) True where the shape continues past the widget (screen edge)."""
        pad = 8
        binary = (cov >= 0.5).astype(np.uint8)
        b = cv2.copyMakeBorder(binary, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
        if cut[0]:
            b[:pad, pad:-pad] = binary[:1]
        if cut[1]:
            b[-pad:, pad:-pad] = binary[-1:]
        d = cv2.distanceTransform(b, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)[pad:-pad, pad:-pad]
        d = np.maximum(d - 0.5 + (cov - 0.5), 0.0).astype(np.float32)        # sub-pixel at the edge
        ds = cv2.GaussianBlur(d, (0, 0), max(0.8, 0.9 * s))
        gx = cv2.Sobel(ds, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(ds, cv2.CV_32F, 0, 1, ksize=3)
        nrm = np.sqrt(gx * gx + gy * gy) + 1e-6
        return d, -gx / nrm, -gy / nrm

    def _field_capsule(self, cap, x0, y0, w, h, s):
        """Analytic stadium (cx, cy, hw, hh in logical px)."""
        cx, cy, hw, hh = cap
        r = min(hw, hh)
        ax, ay = max(0.0, hw - r), max(0.0, hh - r)
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        px = (xs + x0 + 0.5) / s - cx
        py = (ys + y0 + 0.5) / s - cy
        dx = px - np.clip(px, -ax, ax)
        dy = py - np.clip(py, -ay, ay)
        L = np.sqrt(dx * dx + dy * dy) + 1e-6
        d = np.maximum((r - L) * s, 0.0).astype(np.float32)
        return d, (dx / L).astype(np.float32), (dy / L).astype(np.float32)

    # ---- main
    def _bbox(self, br: QRectF, widget_w, widget_h, pad=2):
        s = self.scale
        x0 = max(0, int(math.floor(br.left() * s)) - pad)
        y0 = max(0, int(math.floor(br.top() * s)) - pad)
        x1 = min(int(round(widget_w * s)), int(math.ceil(br.right() * s)) + pad)
        y1 = min(int(round(widget_h * s)), int(math.ceil(br.bottom() * s)) + pad)
        return x0, y0, x1, y1

    def paint(self, p: QPainter, path: QPainterPath, widget_w: int, widget_h: int,
              capsule=None, edge: str = "top"):
        """Draw the glass body for `path` (widget logical coords) with painter p."""
        if self.bg is None:
            return
        s = self.scale
        x0, y0, x1, y1 = self._bbox(path.boundingRect(), widget_w, widget_h)
        w, h = x1 - x0, y1 - y0
        if w < 2 or h < 2:
            return
        cov = self._mask(path, x0, y0, w, h, s)
        if capsule is not None:           # position-independent: a sliding pill reuses it
            cx, cy, hw, hh = capsule
            key = ("cap", w, h, s, round(cx * s - x0, 3), round(cy * s - y0, 3), round(hw, 3), round(hh, 3))
        else:
            key = (x0, y0, w, h, s, cov.tobytes())
        if key != self._field_key:
            if capsule is not None:
                d, nx, ny = self._field_capsule(capsule, x0, y0, w, h, s)
            else:
                cut = (edge == "top" and y0 == 0, edge == "bottom" and y1 >= int(round(widget_h * s)))
                d, nx, ny = self._field_from_mask(cov, s, cut)
            disp = lens(d, s)
            self._field = (d, nx, ny, disp)
            self._field_key = key
        d, nx, ny, disp = self._field
        self._compose(p, x0, y0, w, h, cov, d, nx, ny, disp, cache_key=(key, x0, y0))

    def paint_sdf(self, p: QPainter, sdf_fn, bounds: QRectF, widget_w: int, widget_h: int):
        """Glass for a shape given as a signed distance function (merge blobs).
        sdf_fn(X, Y) takes logical-px grids and returns the signed distance in logical px
        (negative inside)."""
        if self.bg is None:
            return
        s = self.scale
        x0, y0, x1, y1 = self._bbox(bounds, widget_w, widget_h)
        w, h = x1 - x0, y1 - y0
        if w < 2 or h < 2:
            return
        xs = (np.arange(w, dtype=np.float32) + x0 + 0.5) / s
        ys = (np.arange(h, dtype=np.float32) + y0 + 0.5) / s
        X, Y = np.meshgrid(xs, ys)
        sd = sdf_fn(X, Y) * s                                   # device px
        cov = np.clip(0.5 - sd, 0.0, 1.0).astype(np.float32)
        if not (cov > 0).any():
            return
        gy, gx = np.gradient(sd)
        nrm = np.sqrt(gx * gx + gy * gy) + 1e-6
        nx, ny = (gx / nrm).astype(np.float32), (gy / nrm).astype(np.float32)
        d = np.maximum(-sd, 0.0).astype(np.float32)
        self._compose(p, x0, y0, w, h, cov, d, nx, ny, lens(d, s))

    def _shadow(self, p: QPainter, cov, x0, y0, w, h):
        """Soft drop shadow from the coverage mask (used for the merge blobs)."""
        s = self.scale
        m = int(math.ceil(12 * s))
        big = cv2.copyMakeBorder(cov, m, m, m, m, cv2.BORDER_CONSTANT, value=0)
        sh = cv2.GaussianBlur(big, (0, 0), 4.0 * s)
        sh = np.roll(sh, int(round(2 * s)), axis=0)
        a = np.clip(sh * (0.30 * self.shadow_k) * 255.0, 0, 255).astype(np.uint8)
        H, W = a.shape
        out = np.zeros((H, W, 4), np.uint8)
        out[..., 3] = a
        img = QImage(out.data, W, H, W * 4, QImage.Format.Format_ARGB32_Premultiplied)
        p.drawImage(QRectF((x0 - m) / s, (y0 - m) / s, W / s, H / s), img)

    def _compose(self, p, x0, y0, w, h, cov, d, nx, ny, disp, cache_key=None):
        s = self.scale
        amt = float(np.clip(self.amount, 0.0, 1.0))
        # Static backdrop + static shape (e.g. pointer light moving over a settled pill):
        # reuse the refracted / frosted / tinted base and only redo the lighting.
        ck = None
        if cache_key is not None and amt >= 1.0 and not self.ripples:
            ck = (cache_key, self._bg_id)
        c = self._cache
        if ck is not None and c is not None and c[0] == ck:
            self._adapt(c[1])
            tk = (round(self.ink, 3), round(self.lum, 3))
            if c[3] != tk:
                c[2] = self._tint(c[4], d, amt)
                c[3] = tk
            self._light(p, x0, y0, w, h, cov, d, nx, ny, c[2], amt, None)
            return
        if self._blur_id != self._bg_id:
            self._blur = cv2.GaussianBlur(self.bg, (0, 0), FROST * s)
            self._blur_id = self._bg_id
        src = self._blur if amt >= 1.0 else cv2.addWeighted(self.bg, 1.0 - amt, self._blur, amt, 0.0)
        ox, oy = self.origin
        xs = np.arange(w, dtype=np.float32) + (x0 - ox)
        ys = np.arange(h, dtype=np.float32) + (y0 - oy)
        X, Y = np.meshgrid(xs, ys)
        if amt < 1.0:
            disp = disp * amt
        ring_total = None
        if self.ripples:                       # click shimmer: a bright ring that also bends
            gx = (np.arange(w, dtype=np.float32) + x0 + 0.5) / s
            gyv = (np.arange(h, dtype=np.float32) + y0 + 0.5) / s
            GX, GY = np.meshgrid(gx, gyv)
            ring_total = np.zeros((h, w), np.float32)
            for (rx, ry, R, k) in self.ripples:
                dx, dy = GX - rx, GY - ry
                r = np.sqrt(dx * dx + dy * dy) + 1e-3
                ring = k * np.exp(-((r - R) / 7.0) ** 2)
                ring_total += ring
                X = X + dx / r * ring * (2.5 * s)
                Y = Y + dy / r * ring * (2.5 * s)
        disp_k = DISPERSION * amt
        chans = []
        for ch, k in ((0, 1.0 - disp_k), (1, 1.0), (2, 1.0 + disp_k)):   # B, G, R
            dd = disp * k
            chans.append(cv2.remap(src[:, :, ch], X + nx * dd, Y + ny * dd, cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE))
        col = np.dstack(chans).astype(np.float32) * (1.0 / 255.0)

        # luminance of the backdrop under the flat part (what the text sits on)
        lumimg = col[..., 0] * 0.0722 + col[..., 1] * 0.7152 + col[..., 2] * 0.2126
        inner = (cov > 0.5) & (d > BEVEL * s * 0.6)
        if not inner.any():
            inner = cov > 0.5
        lum_now = float(lumimg[inner].mean()) if inner.any() else 0.3
        self._adapt(lum_now)

        # frost: saturation lift
        g = lumimg[..., None]
        col = g + (col - g) * (1.0 + (SATURATION - 1.0) * amt)
        base = col
        col = self._tint(col, d, amt)
        self._cache = None
        if ck is not None:
            self._cache = [ck, lum_now, col, (round(self.ink, 3), round(self.lum, 3)), base]
        self._light(p, x0, y0, w, h, cov, d, nx, ny, col, amt, ring_total)

    def _tint(self, col, d, amt):
        s = self.scale
        # adaptive tint + contrast backing (strongest under the text, lighter at the rim)
        rim_w = (_smooth(0.0, BEVEL * s, d) * 0.65 + 0.35)[..., None] * amt
        lum = self.lum
        dark_amt = float(np.clip(0.10 + (lum - 0.22) * 0.85, 0.10, 0.48))     # under light text
        light_amt = float(np.clip(0.16 + (0.78 - lum) * 1.1, 0.16, 0.55))    # under dark text
        ink = self.ink
        col = col * (1 - (1 - ink) * dark_amt * rim_w)                        # darken toward black
        col = col + (1 - col) * (ink * light_amt * rim_w)                     # lighten toward white
        col = col + (1 - col) * (0.04 * amt)                                  # glass is a touch milky
        self.backing = (1 - ink) * dark_amt + ink * light_amt
        return col

    def _light(self, p, x0, y0, w, h, cov, d, nx, ny, col, amt, ring_total):
        s = self.scale
        # light: inner shadow + specular rim (light direction may follow the pointer)
        lx, ly = self.light if self.light is not None else LIGHT
        facing = nx * lx + ny * ly                    # +1 rim faces the light
        lit = np.clip(facing, 0, 1) ** 1.6
        far = np.clip(-facing, 0, 1) ** 1.6
        dc = self._dcache
        if dc is None or dc[0] is not d:              # distance-only terms, once per shape
            thin = np.clip(1.0 - d / (1.5 * s), 0, 1)
            dc = self._dcache = (d, thin, np.exp(-d / (4.5 * s)),
                                 INNER_SHADOW * np.exp(-d / (5.0 * s)) * (1 - thin))
        _, thin, glow, shade0 = dc
        shade = shade0 * (0.35 + 0.65 * far)
        col = col * (1 - (shade * amt)[..., None])
        spec = thin * (SPEC_RIM * lit + SPEC_FAR * far + SPEC_BASE) + glow * SPEC_GLOW * lit ** 2
        spec = spec * (amt * self.spec_k)
        if self.glow is not None:                     # pointer: faint radial glow under the cursor
            gx0, gy0, gk = self.glow
            px = (np.arange(w, dtype=np.float32) + x0 + 0.5) / s - gx0
            py = (np.arange(h, dtype=np.float32) + y0 + 0.5) / s - gy0
            k2 = 1.0 / (2 * 26.0 ** 2)                # separable gaussian
            spec = spec + gk * (np.exp(-py * py * k2)[:, None] * np.exp(-px * px * k2)[None, :])                 * (0.6 + 0.4 * glow)
        if ring_total is not None:
            spec = spec + 0.30 * ring_total
        col = col + (1 - col) * np.clip(spec, 0, 1)[..., None]

        if self.mask_shadow:
            self._shadow(p, cov, x0, y0, w, h)
        a = cov[..., None]
        out = np.empty((h, w, 4), np.uint8)
        out[..., :3] = np.clip(col * a * 255.0 + 0.5, 0, 255)
        out[..., 3] = np.clip(cov * 255.0 + 0.5, 0, 255)
        img = QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(x0 / s, y0 / s, w / s, h / s), img)
        p.restore()

    def _adapt(self, lum_now: float):
        now = time.perf_counter()
        dt = 0.0 if self._t is None else min(0.2, now - self._t)
        self._t = now
        if self.lum is None or dt == 0.0:
            self.lum = lum_now
            self.ink_target = 1.0 if lum_now > (LUM_TO_DARK + LUM_TO_LIGHT) / 2 else 0.0
            self.ink = self.ink_target
            return
        self.lum += (lum_now - self.lum) * (1 - math.exp(-dt / LUM_TIME))
        if self.ink_target < 0.5 and self.lum > LUM_TO_DARK:
            self.ink_target = 1.0
        elif self.ink_target > 0.5 and self.lum < LUM_TO_LIGHT:
            self.ink_target = 0.0
        step = dt / INK_TIME
        self.ink = min(self.ink_target, self.ink + step) if self.ink < self.ink_target \
            else max(self.ink_target, self.ink - step)

    @property
    def settling(self) -> bool:
        """True while the content colour is still crossing over (needs more frames)."""
        return abs(self.ink - self.ink_target) > 1e-3
