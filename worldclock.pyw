"""WorldClock - an auto-hiding 'island' world clock for the screen edge.

Usage:
    pythonw worldclock.pyw                      normal run (tray + hidden pill)
    pythonw worldclock.pyw --watchdog           same, restarted if it crashes (autostart uses this)
    python  worldclock.pyw --crash-test exception|segfault|exit|quit|slot   test the crash log
    python  worldclock.pyw --screenshot out.png [--force-holiday GE] [--force-night]
    python  worldclock.pyw --dump               print computed values and exit
    python  worldclock.pyw --frames DIR [--edge bottom]   render the liquid reveal (PNGs, sheet, GIF, MP4)
    add --style glass --bg image.png to either to render the Liquid Glass look over an image
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)          # running as the PyInstaller exe
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(HERE))

if __name__ == "__main__" and "--watchdog" in sys.argv[1:]:
    # restart-on-crash supervisor: stays light, so it must run before Qt/numpy/cv2 load
    import supervisor
    sys.exit(supervisor.run(sys.argv[1:]))

import crashlog

from PySide6.QtCore import (Qt, QTimer, QPoint, QPointF, QRect, QRectF, QSize, QPropertyAnimation,
                            QEasingCurve, QDir, qVersion)
from PySide6.QtGui import (QCursor, QGuiApplication, QImage, QPainter, QColor, QLinearGradient,
                           QAction, QPen)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

import anim_modes
import clockdata
import glass
import glass_fx
import settings as settings_ui
import ui
from version import __version__

APP_NAME = "WorldClock"
DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
SETTINGS = DATA_DIR / "settings.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

EDGE_GAP = ui.EDGE_GAP  # px between screen edge and the pill when shown
HOT_MARGIN = 8        # extra px left/right of the pill that still trigger
POLL_MS = 40
ANIM_MS = 180         # only the snap back after a drag
FRAME_MS = 16         # liquid reveal tick (~60 fps)
LEAVE_MS = 500
IN_SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def clean_speed(v) -> float:
    try:
        return min(2.0, max(0.5, float(v)))
    except (TypeError, ValueError):
        return 1.0
GLASS_POLL_MS = 33    # glass: how often the GUI picks up a new backdrop grab (~30 fps)
POINTER_FRAME_S = 1 / 62.0   # pointer-light repaint cap while the pill is settled


# ---------------------------------------------------------------- settings
def load_settings() -> tuple[dict, bool]:
    try:
        return json.loads(SETTINGS.read_text("utf-8")), False
    except FileNotFoundError:
        return {}, True
    except Exception:
        return {}, False


def save_settings(d: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(d, indent=2), "utf-8")


# ---------------------------------------------------------------- autostart
def _autostart_cmd() -> str:
    """Autostart launches the watchdog, which runs (and restarts) the real app."""
    if FROZEN:                                      # installed build: the exe itself
        return f'"{Path(sys.executable).resolve()}" --watchdog'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if pyw.exists():
        exe = pyw
    return f'"{exe}" "{Path(__file__).resolve()}" --watchdog'


def autostart_value() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return str(winreg.QueryValueEx(k, APP_NAME)[0])
    except OSError:
        return None


def autostart_enabled() -> bool:
    return autostart_value() is not None


def migrate_autostart():
    """Frozen build: keep an existing Run entry pointing at this exe with --watchdog
    (1.0.0 wrote the bare exe). Only touch it when it names this exe, or an exe that no
    longer exists (the install moved), so a test build elsewhere can't hijack it."""
    cur = autostart_value()
    if not FROZEN or cur is None or cur == _autostart_cmd():
        return
    here = Path(sys.executable).resolve()
    target = cur.strip()
    target = target[1:].split('"', 1)[0] if target.startswith('"') else target.split(" ", 1)[0]
    try:
        same = Path(target).resolve() == here
    except OSError:
        same = False
    if same or not Path(target).exists():
        set_autostart(True)
        crashlog.log.info("Autostart updated: %s", _autostart_cmd())


def set_autostart(on: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


# ---------------------------------------------------------------- window
class ClockWindow(ui.PillWidget):
    """One pill. In "all monitors" mode there is one per screen (fixed_screen); a
    ClockSet (self.manager) keeps them in sync. Standalone use (screenshots) works too."""

    def __init__(self, settings: dict, force_holiday=None, force_night=False, screen=None):
        super().__init__()
        self.fixed_screen = screen
        self.manager = None
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.force_holiday = force_holiday or set()
        self.force_night = force_night

        self.settings = settings
        self.edge = settings.get("edge", "top")
        self.clocks = clockdata.clocks_from_settings(settings)
        self.cities = clockdata.cities_for(self.clocks)
        self.set_columns(len(self.cities))
        for k in glass_fx.FX_KEYS:
            self.fx[k] = bool(settings.get(glass_fx.FX_SETTING[k], False))
        self.set_anim(settings.get("anim", "liquid"))
        self.in_speed = clean_speed(settings.get("in_speed", 1.0))
        self.liq.reset_hidden()
        self._ptr_dirty = False
        self.grabber = None                       # glass backdrop grabber (live only)
        self.glass_timer = QTimer(self, interval=GLASS_POLL_MS)
        self.glass_timer.timeout.connect(self._glass_poll)
        self._glass_seq = -1
        self.set_style(settings.get("style", "dark"))
        self.screen_name = settings.get("screen")
        self.x_frac = settings.get("x_frac", 0.5)   # pill centre as a fraction of the screen width
        self.pinned = False
        self.temp_until = 0.0
        self.state = "hidden"                     # hidden | showing | shown | hiding
        self.dragging = False
        self.drag_off = QPoint()
        self.press_pos = QPoint()
        self.last_minute = None

        self.balloon = ui.Balloon()
        self.balloon.setWindowFlags(Qt.WindowType.FramelessWindowHint
                                    | Qt.WindowType.WindowStaysOnTopHint
                                    | Qt.WindowType.Tool
                                    | Qt.WindowType.WindowTransparentForInput)
        self.balloon.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.balloon.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.balloon_col = -1

        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(ANIM_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._anim_done)

        self.frame_timer = QTimer(self, interval=FRAME_MS)
        self.frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.frame_timer.timeout.connect(self._frame)
        self.last_frame = 0.0

        self.hide_delay = self._clean_delay(settings.get("hide_delay", LEAVE_MS / 1000))
        self.leave_timer = QTimer(self, singleShot=True, interval=int(self.hide_delay * 1000))
        self.leave_timer.timeout.connect(self._leave_fire)
        self.poll = QTimer(self, interval=POLL_MS)
        self.poll.timeout.connect(self._poll)
        self.tick = QTimer(self, interval=1000)
        self.tick.timeout.connect(self.refresh)

        self.refresh(force=True)

    # ---- data
    def refresh(self, force=False):
        now = datetime.now(timezone.utc)
        minute = now.replace(second=0, microsecond=0)
        if force or minute != self.last_minute:
            self.last_minute = minute
            self.states = clockdata.compute(now, self.force_holiday, self.force_night, self.cities)
            self.update()
            if self.balloon_col >= 0:
                self._update_balloon(self.balloon_col, force=True)

    def apply_clocks(self, clocks: list[dict], persist: bool = True):
        """Live-apply a new clock list (from the settings dialog)."""
        self.clocks = [dict(c) for c in clocks][:clockdata.MAX_CLOCKS]
        self.cities = clockdata.cities_for(self.clocks)
        old_w = self.pill_w
        self.balloon.hide()
        self.balloon_col = -1
        self.set_columns(len(self.cities))      # x_frac is the pill centre, so it stays centred
        if self.state in ("shown", "showing") and not self.dragging:
            self.anim.stop()
            self.move(self.shown_pos())
        self.refresh(force=True)
        if persist:
            self.persist()

    @staticmethod
    def _clean_delay(v) -> float:
        try:
            return min(60.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return LEAVE_MS / 1000

    def apply_hide_delay(self, seconds: float, persist: bool = True):
        """How long the pill stays after the mouse leaves (live; a running countdown
        restarts with the new length)."""
        self.hide_delay = self._clean_delay(seconds)
        running = self.leave_timer.isActive()
        self.leave_timer.setInterval(int(self.hide_delay * 1000))
        if running:
            self.leave_timer.start()
        if persist:
            self.persist()

    def apply_anim(self, kind: str, preview: bool = True, persist: bool = True):
        """Switch the reveal animation live; optionally replay it once on the pill."""
        busy = self.frame_timer.isActive()
        self.set_anim(kind)
        if persist:
            self.persist()
        if self.dragging:
            return
        if preview and self.state in ("shown", "showing"):
            self.anim.stop()
            self.move(self.shown_pos())
            self.liq.reset_hidden()
            self.liq.set_mode("show")
            self.state = "showing"
            self._run_frames()
        elif busy or self.state in ("showing", "hiding"):
            self._run_frames()          # let the new model finish the current transition

    # ---- glass
    def set_style(self, style: str):
        super().set_style(style)
        if self.style != "glass":
            self.glass.bg = None            # dark look; no capture
            self.glass_timer.stop()
            if self.grabber is not None:
                self.grabber.set_target(None)

    def apply_in_speed(self, speed: float, persist: bool = True):
        """Settings > In speed (live): replays a reveal at the new speed, like Animation."""
        self.in_speed = clean_speed(speed)
        if persist:
            self.persist()
        if self.dragging:
            return
        if self.state in ("shown", "showing"):
            self.anim.stop()
            self.move(self.shown_pos())
            self.liq.reset_hidden()
            self.liq.set_mode("show")
            self.state = "showing"
            self._run_frames()

    def apply_fx(self, key: str, on: bool, persist: bool = True):
        """Toggle a Glass effect live (materialize / merge / pointer)."""
        if key not in glass_fx.FX_KEYS:
            return
        self.set_fx(key, on)
        if persist:
            self.persist()
        self._sync_mode()
        self.update()

    def _sync_mode(self):
        """After the reveal model was rebuilt / re-wrapped mid-transition: point it the same
        way the window is going and let it finish."""
        if self.dragging:
            return
        if self.state in ("showing", "shown") and self.liq.mode != "show":
            self.liq.set_mode("show")
            self.state = "showing"
        elif self.state == "hiding" and self.liq.mode != "hide":
            self.liq.set_mode("hide")
        if self.state in ("showing", "hiding") or (self.state == "shown" and not self.liq.is_settled):
            if self.state == "shown":
                self.state = "showing"
            self._run_frames()

    def fx_settings(self) -> dict:
        return {glass_fx.FX_SETTING[k]: bool(self.fx[k]) for k in glass_fx.FX_KEYS}

    def _set_affinity(self):
        """Glass: keep the pill (and its balloon) out of screen captures, so grabbing the
        screen under it never sees the pill itself. Dark: normal (shows in screenshots)."""
        on = self.style == "glass"
        self.glass_excluded = glass.set_exclude_from_capture(int(self.winId()), on)
        glass.set_exclude_from_capture(int(self.balloon.winId()), on)

    def apply_style(self, style: str, persist: bool = True):
        self.set_style(style)
        self._sync_mode()
        self._set_affinity()
        if self.style == "glass" and self.state != "hidden":
            self._glass_grab_now()
            self._glass_start()
        self.update()
        if persist:
            self.persist()

    def _glass_grab_now(self):
        """Synchronous grab (used before the first frame, when there is no frame yet)."""
        try:
            hwnd = int(self.winId())
            res = glass.grab_once(hwnd)
        except Exception:
            res = None
        if res is None:
            return
        img, want = res
        wl, wt_, _, _ = glass.window_rect(hwnd)
        self.glass.set_backdrop(img, (want[0] - wl, want[1] - wt_), self.devicePixelRatioF())

    def _glass_start(self):
        if self.style != "glass":
            return
        if self.grabber is None:
            self.grabber = glass.Grabber()
        self.grabber.set_target(int(self.winId()))
        if not self.glass_timer.isActive():
            self.glass_timer.start()

    def _glass_stop(self):
        self.glass_timer.stop()
        if self.grabber is not None:
            self.grabber.set_target(None)

    def _glass_poll(self):
        if self.style != "glass" or self.state == "hidden":
            self._glass_stop()
            return
        fr = self.grabber.latest() if self.grabber is not None else None
        dirty = self.glass.settling
        if fr is not None and fr[0] != self._glass_seq:
            seq, img, want = fr
            self._glass_seq = seq
            wl, wt_, _, _ = glass.window_rect(int(self.winId()))
            self.glass.set_backdrop(img, (want[0] - wl, want[1] - wt_), self.devicePixelRatioF())
            dirty = True
        if dirty and not self.frame_timer.isActive():
            self.update()

    def start(self):
        self._set_affinity()
        self._warm_up()
        self.place_hidden()
        self.tick.start()
        self.poll.start()

    def _warm_up(self):
        """Paint one settled frame offscreen so fonts/glyphs are loaded before the first
        reveal (otherwise the first text frame stalls the animation for ~0.5 s)."""
        saved = self.liq
        import copy
        self.liq = copy.copy(saved)
        self.liq.reset_shown()
        img = QImage(self.width() * 2, self.height() * 2, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(2)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        self.paint_all(p)
        p.end()
        self.liq = saved

    # ---- geometry
    def target_screen(self):
        screens = QGuiApplication.screens()
        if self.fixed_screen is not None:
            for s in screens:
                if s is self.fixed_screen:
                    return s
            return QGuiApplication.primaryScreen()
        for s in screens:
            if s.name() == self.screen_name:
                return s
        return QGuiApplication.primaryScreen()

    def _x_for(self, scr) -> int:
        g = scr.availableGeometry()
        frac = 0.5 if self.x_frac is None else float(self.x_frac)
        pill_x = int(round(g.left() + frac * g.width() - self.pill_w / 2))
        pill_x = max(g.left() + 4, min(pill_x, g.right() - self.pill_w - 4))
        return pill_x - self.side

    def shown_pos(self) -> QPoint:
        """The window never moves while revealing: its edge side sits on the screen edge."""
        scr = self.target_screen()
        g = scr.availableGeometry()
        x = self._x_for(scr)
        y = g.bottom() + 1 - self.height() if self.edge == "bottom" else g.top()
        return QPoint(x, y)

    def pill_global(self) -> QRect:
        return QRect(self.pos() + self.pill_origin(), QSize(self.pill_w, ui.PILL_H))

    def place_hidden(self):
        self.anim.stop()
        self.frame_timer.stop()
        self.liq.reset_hidden()
        self.move(self.shown_pos())
        self.state = "hidden"
        self._glass_stop()
        self.hide()
        self.balloon.hide()

    def screens_changed(self, *_):
        # clamp to the (possibly new) layout
        if self.dragging:
            return
        if self.state in ("shown", "showing"):
            self.anim.stop()
            self.frame_timer.stop()
            self.move(self.shown_pos())
            self.liq.reset_shown()
            self.state = "shown"
            self.update()
        else:
            self.place_hidden()

    # ---- liquid reveal: all motion happens in paintEvent, the window stays put
    def slide_out(self):
        if self.state in ("shown", "showing"):
            return
        if self.state == "hidden":
            self.anim.stop()
            self.liq.reset_hidden()
            self.move(self.shown_pos())
            if self.style == "glass":
                self.glass.reset_adapt()
                self._glass_grab_now()      # window still hidden: this grab can't see it
            self.show()
            self.raise_()
        self._glass_start()
        self.liq.set_mode("show")
        self.state = "showing"
        self._run_frames()

    def slide_in(self):
        if self.state in ("hidden", "hiding") or self.dragging:
            return
        self.balloon.hide()
        self.balloon_col = -1
        self.anim.stop()
        self.pointer.hover_target = 0.0
        self.pointer.release()
        self.liq.set_mode("hide")
        self.state = "hiding"
        self._run_frames()

    def _run_frames(self):
        if not self.frame_timer.isActive():
            # tick at the display's refresh rate; motion itself is purely time-based
            hz = self.target_screen().refreshRate() or 60.0
            self.frame_timer.setInterval(max(4, int(1000.0 / min(max(hz, 30.0), 240.0))))
            self.last_frame = time.perf_counter()
            self.frame_timer.start()

    def _frame(self):
        now = time.perf_counter()
        if self.state == "shown" and now - self.last_frame < POINTER_FRAME_S:
            return                  # pointer light only: 60 fps is plenty, even on 144 Hz
        dt = min(now - self.last_frame, 0.05)
        self.last_frame = now
        self.step_model(self.liq, dt)
        fx_busy = self.pointer.step(dt) if self.fx_on("pointer") else False
        self.update()
        if self.state == "showing" and self.liq.is_settled:
            self.state = "shown"
            if not fx_busy:
                self.frame_timer.stop()
        elif self.state == "hiding" and self.liq.is_hidden:
            self.frame_timer.stop()
            self.state = "hidden"
            self.pointer.reset()
            self._glass_stop()
            self.hide()
        elif self.state == "shown" and not fx_busy and not self._ptr_dirty:
            self.frame_timer.stop()     # pointer light idle: no more frames until it moves
        self._ptr_dirty = False

    def _anim_done(self):
        pass

    def show_for(self, seconds: float):
        self.temp_until = time.monotonic() + seconds
        self.slide_out()

    def set_pinned(self, on: bool):
        self.pinned = on
        if on:
            self.slide_out()

    def reset_position(self, screen=None):
        """Top edge, centred, on `screen` (default: the primary monitor)."""
        self.set_position("top", 0.5, (screen or QGuiApplication.primaryScreen()).name())
        self.persist()
        self.place_hidden()
        self.show_for(3)

    def set_position(self, edge: str, x_frac: float, screen_name: str | None):
        self.edge, self.x_frac = edge, x_frac
        if screen_name:
            self.screen_name = screen_name
        self.update()

    def persist(self):
        if self.manager is not None:
            self.manager.persist_from(self)
            return
        self.settings.update(edge=self.edge, screen=self.screen_name, x_frac=self.x_frac,
                             clocks=self.clocks, anim=self.anim_kind,
                             hide_delay=self.hide_delay, style=self.style, in_speed=self.in_speed,
                             **self.fx_settings())
        self.settings.pop("x", None)
        save_settings(self.settings)

    # ---- hot zone / leave polling
    def _in_hot_zone(self, c: QPoint) -> bool:
        scr = self.target_screen()
        g = scr.geometry()
        pill_l = self._x_for(scr) + self.side
        if not (pill_l - HOT_MARGIN <= c.x() <= pill_l + self.pill_w + HOT_MARGIN):
            return False
        if not (g.left() <= c.x() <= g.right()):
            return False
        # only real outer edges of the desktop: if another monitor continues past this
        # edge the cursor never "hits" it, so it must not trigger either
        if self.edge == "bottom":
            return c.y() >= g.bottom() and QGuiApplication.screenAt(QPoint(c.x(), g.bottom() + 1)) is None
        return c.y() <= g.top() and QGuiApplication.screenAt(QPoint(c.x(), g.top() - 1)) is None

    def _over_pill(self, c: QPoint) -> bool:
        r = self.pill_global()
        # include the gap to the edge so moving back up doesn't trigger a hide
        r = r.adjusted(-4, -EDGE_GAP - 4, 4, EDGE_GAP + 4)
        return r.contains(c)

    def _poll(self):
        c = QCursor.pos()
        if self.state in ("hidden", "hiding"):
            if self.pinned or time.monotonic() < self.temp_until or self._in_hot_zone(c):
                self.slide_out()
            return
        # shown / showing
        keep = (self.pinned or self.dragging or time.monotonic() < self.temp_until
                or self._over_pill(c) or self._in_hot_zone(c))
        if keep:
            self.leave_timer.stop()
        elif not self.leave_timer.isActive():
            self.leave_timer.start()
        if self.balloon.isVisible() and not self._over_pill(c):
            self.balloon.hide()
            self.balloon_col = -1
        if self.pointer.hover_target > 0 and not self._over_pill(c):
            self.pointer.hover_target = 0.0
            self._run_frames()

    def _leave_fire(self):
        c = QCursor.pos()
        if not (self.pinned or self.dragging or time.monotonic() < self.temp_until
                or self._over_pill(c) or self._in_hot_zone(c)):
            self.slide_in()

    # ---- balloon
    def _update_balloon(self, col: int, force=False):
        if col < 0 or col >= len(self.states) or not self.states[col].holiday \
                or self.dragging or self.state != "shown":
            self.balloon.hide()
            self.balloon_col = -1
            return
        if col == self.balloon_col and not force and self.balloon.isVisible():
            return
        self.balloon_col = col
        head, rest = ui.balloon_text(self.states[col])
        below = self.edge == "top"
        self.balloon.set_content(head, rest, pointer_up=below)
        cr = self.column_rect(col)
        anchor_x = self.x() + cr.center().x()
        bw, bh = self.balloon.width(), self.balloon.height()
        g = self.target_screen().availableGeometry()
        bx = int(anchor_x - bw / 2)
        bx = max(g.left() + 2, min(bx, g.right() - bw - 2))
        pg = self.pill_global()
        if below:
            by = pg.top() + ui.PILL_H + 2 - ui.Balloon.SH
        else:
            by = pg.top() - 2 - bh + ui.Balloon.SH
        self.balloon.tail_x = anchor_x - bx
        self.balloon.move(bx, by)
        self.balloon.show()
        self.balloon.update()

    # ---- mouse
    # ---- pointer light (Glass effect)
    def _pointer_live(self) -> bool:
        return self.fx_on("pointer") and self.glass_on and self.state in ("shown", "showing")

    def _pointer_move(self, pos: QPointF):
        if not self._pointer_live():
            return
        self.pointer.pos = (pos.x(), pos.y())
        self.pointer.hover_target = 1.0 if self.pill_rect().contains(pos) else 0.0
        self._ptr_dirty = True
        self._run_frames()              # repaint at most once per display frame

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._pointer_live() \
                and self.pill_rect().contains(e.position()):
            self.pointer.pos = (e.position().x(), e.position().y())
            self.pointer.press(e.position().x(), e.position().y())
            self._run_frames()
        if e.button() == Qt.MouseButton.LeftButton:
            if self.state != "shown" or not self.liq.is_settled:
                return              # drag only once the liquid has settled
            self.dragging = True
            self.anim.stop()
            self.state = "shown"
            self.drag_off = e.globalPosition().toPoint() - self.pos()
            self.balloon.hide()
            self.balloon_col = -1
        elif e.button() == Qt.MouseButton.RightButton and self.context_menu:
            if self.manager is not None:
                self.manager.menu_window = self
            self.context_menu.popup(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        self._pointer_move(e.position())
        if self.dragging:
            self.move(e.globalPosition().toPoint() - self.drag_off)
            return
        self._update_balloon(self.column_at(e.position()))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.pointer.sq_target > 0:
            self.pointer.release()
            self._run_frames()
        if e.button() != Qt.MouseButton.LeftButton or not self.dragging:
            return
        self.dragging = False
        c = e.globalPosition().toPoint()
        scr = QGuiApplication.screenAt(c) or self.target_screen()
        g = scr.geometry()
        pill_tl = self.pos() + self.pill_origin()
        self.edge = "bottom" if c.y() > g.center().y() else "top"
        self.move(pill_tl - self.pill_origin())   # pill stays put if the edge flipped
        self.update()
        if self.fixed_screen is None:
            self.screen_name = scr.name()
        else:
            scr = self.target_screen()           # in "all" mode each pill keeps its own screen
        ag = scr.availableGeometry()
        centre = pill_tl.x() + self.pill_w / 2
        self.x_frac = min(1.0, max(0.0, (centre - ag.left()) / max(1, ag.width())))
        self.x_frac = (self._x_for(scr) + self.side + self.pill_w / 2 - ag.left()) / ag.width()
        self.persist()
        if self.manager is not None:
            self.manager.position_changed(self)
        self.anim.stop()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(self.shown_pos())
        self.anim.start()

    def leaveEvent(self, _e):
        if self.pointer.hover_target > 0:
            self.pointer.hover_target = 0.0
            self._run_frames()
        if self.balloon.isVisible():
            self.balloon.hide()
            self.balloon_col = -1

    def shutdown(self):
        """Remove this pill for good (its monitor went away / mode changed)."""
        for t in (self.frame_timer, self.leave_timer, self.poll, self.tick, self.glass_timer):
            t.stop()
        if self.grabber is not None:
            self.grabber.stop()
        self.anim.stop()
        self.balloon.hide()
        self.hide()
        self.balloon.deleteLater()
        self.deleteLater()

    context_menu: QMenu | None = None


# ---------------------------------------------------------------- the set of pills
MENU_STYLE = """
QMenu { background: #141417; border: 1px solid rgba(255,255,255,32); border-radius: 10px;
        padding: 6px; color: #e9e9ec; font-family: 'Segoe UI Variable Text', 'Segoe UI';
        font-size: 13px; }
QMenu::item { padding: 7px 28px 7px 14px; border-radius: 6px; background: transparent; }
QMenu::item:selected { background: rgba(255,255,255,24); color: #ffffff; }
QMenu::item:disabled { color: rgba(255,255,255,70); }
QMenu::separator { height: 1px; background: rgba(255,255,255,20); margin: 5px 8px; }
QMenu::indicator { width: 14px; height: 14px; left: 6px; }
"""


def style_menu(menu: QMenu):
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                        | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    menu.setStyleSheet(MENU_STYLE)


class ClockSet:
    """One pill ("one" monitor mode) or one per screen ("all"). Settings changes, the
    shared position (edge + horizontal fraction) and pinning fan out to every pill."""

    def __init__(self, settings: dict, force_holiday=None, force_night=False):
        self.settings = settings
        self.fh, self.fn = force_holiday or set(), force_night
        self.mode = settings.get("monitors", "one")
        if self.mode not in ("one", "all"):
            self.mode = "one"
        self.windows: list[ClockWindow] = []
        self.menu_window: ClockWindow | None = None
        self.context_menu: QMenu | None = None
        self.pinned = False

    # ---- windows
    def _new_window(self, screen):
        w = ClockWindow(self.settings, self.fh, self.fn, screen=screen)
        w.manager = self
        w.context_menu = self.context_menu
        return w

    def sync_windows(self, *_):
        screens = list(QGuiApplication.screens())
        want = screens if self.mode == "all" else [None]
        keep = []
        for w in self.windows:
            ok = (w.fixed_screen is None) if self.mode != "all" else \
                any(w.fixed_screen is s for s in screens)
            if ok:
                keep.append(w)
            else:
                w.shutdown()
        self.windows = keep
        for s in want:
            if not any(w.fixed_screen is s for w in self.windows):
                w = self._new_window(s)
                self.windows.append(w)
                w.start()
                if self.pinned:
                    w.set_pinned(True)
        for w in self.windows:
            w.screens_changed()

    def start(self):
        self.sync_windows()

    @property
    def first(self) -> "ClockWindow":
        return self.windows[0]

    # ---- settings fan-out
    def save(self):
        w = self.first
        self.settings.update(edge=w.edge, screen=w.screen_name, x_frac=w.x_frac,
                             clocks=w.clocks, anim=w.anim_kind, hide_delay=w.hide_delay,
                             monitors=self.mode, style=w.style, in_speed=w.in_speed,
                             **w.fx_settings())
        self.settings.pop("x", None)
        save_settings(self.settings)

    def persist_from(self, _w):
        self.save()

    def apply_clocks(self, clocks):
        for w in self.windows:
            w.apply_clocks(clocks, persist=False)
        self.save()

    def apply_anim(self, kind):
        for w in self.windows:
            w.apply_anim(kind, preview=True, persist=False)
        self.save()

    def apply_style(self, style):
        self.settings["style"] = style
        for w in self.windows:
            w.apply_style(style, persist=False)
        self.save()

    def apply_in_speed(self, speed):
        for w in self.windows:
            w.apply_in_speed(speed, persist=False)
        self.save()

    def apply_fx(self, key, on):
        for w in self.windows:
            w.apply_fx(key, on, persist=False)
        self.save()

    def apply_hide_delay(self, seconds):
        for w in self.windows:
            w.apply_hide_delay(seconds, persist=False)
        self.save()

    def set_monitors(self, mode):
        self.mode = "all" if mode == "all" else "one"
        if self.mode == "one":
            # the single pill goes to the screen the mouse is on
            scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            self.settings["screen"] = scr.name()
            for w in self.windows:
                w.screen_name = scr.name()
        self.save()
        self.sync_windows()

    def set_pinned(self, on):
        self.pinned = on
        for w in self.windows:
            w.set_pinned(on)

    def show_for(self, seconds):
        for w in self.windows:
            w.show_for(seconds)

    def reset_position(self, screen=None):
        """Top edge, centred; `screen` matters in "one" mode (default: primary)."""
        name = (screen or QGuiApplication.primaryScreen()).name()
        for w in self.windows:
            w.set_position("top", 0.5, name)
            w.place_hidden()
        self.save()
        self.show_for(3)

    def reset_from_menu(self):
        w = self.menu_window or self.first
        self.reset_position(w.target_screen())

    def position_changed(self, src):
        """A pill was dragged: every other pill follows to the same relative spot."""
        for w in self.windows:
            if w is src:
                continue
            w.set_position(src.edge, src.x_frac, src.screen_name)
            if w.state in ("shown", "showing") and not w.dragging:
                w.anim.stop()
                w.anim.setStartValue(w.pos())
                w.anim.setEndValue(w.shown_pos())
                w.anim.start()
            elif w.state == "hidden":
                w.move(w.shown_pos())
        self.save()


# ---------------------------------------------------------------- screenshot
DEMO_IDS = ["los-angeles-us", "new-york-us", "sydney-au", "berlin-de",
            "london-gb", "tokyo-jp", "dubai-ae", "sao-paulo-br"]


def demo_clocks(n: int | None) -> dict:
    """Settings for screenshots: n clocks (defaults first, then a spread of cities)."""
    if not n:
        return {}
    clocks = clockdata.clocks_from_settings({})
    for cid in DEMO_IDS[len(clocks):]:
        clocks.append({"id": cid, "label": clockdata.db_entry(cid)["label"], "holidays": True})
    return {"clocks": clocks[:n]}


def render_settings_shot(out: str, search: str | None, style: str = "dark"):
    dlg = settings_ui.SettingsDialog(clockdata.clocks_from_settings({}), "liquid", 1.5,
                                     style=style, fx=FX_DEMO)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.show()
    QApplication.processEvents()
    if search is not None:
        dlg.open_search()
        dlg.search.setText(search)
        QApplication.processEvents()
    dlg.adjustSize()
    QApplication.processEvents()
    scale, pad = 2, 20
    W, H = dlg.width() + 2 * pad, dlg.height() + 2 * pad
    img = QImage(W * scale, H * scale, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(scale)
    p = QPainter(img)
    _bg(p, W, H)
    dlg.render(p, QPoint(pad, pad), renderFlags=QWidget.RenderFlag.DrawChildren)
    p.end()
    img.save(out)
    print("saved", out)


class Backdrop:
    """--bg: an image under the clock for offscreen glass renders (scaled to cover)."""

    def __init__(self, path: str):
        import cv2
        import numpy as np
        self.cv2, self.np = cv2, np
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"cannot read --bg {path}")
        self.img = img

    def canvas(self, W: int, H: int, scale: float):
        """BGR array (device px) covering a W x H logical canvas."""
        cw, ch = int(round(W * scale)), int(round(H * scale))
        ih, iw = self.img.shape[:2]
        k = max(cw / iw, ch / ih)
        r = self.cv2.resize(self.img, (max(cw, int(math.ceil(iw * k))), max(ch, int(math.ceil(ih * k)))),
                            interpolation=self.cv2.INTER_AREA if k < 1 else self.cv2.INTER_CUBIC)
        y0, x0 = (r.shape[0] - ch) // 2, (r.shape[1] - cw) // 2
        return self.np.ascontiguousarray(r[y0:y0 + ch, x0:x0 + cw])

    def paint(self, p: QPainter, arr, W: int, H: int):
        h, w = arr.shape[:2]
        rgb = self.np.ascontiguousarray(arr[:, :, ::-1])
        q = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
        p.drawImage(QRectF(0, 0, W, H), q)


def attach_backdrop(w, arr, wx: float, wy: float, scale: float, W: int, H: int):
    """Give the widget at logical (wx, wy) on the canvas its glass backdrop."""
    m = int(math.ceil(glass.MARGIN * scale))
    import cv2
    big = cv2.copyMakeBorder(arr, m, m, m, m, cv2.BORDER_REPLICATE)
    w.glass.set_backdrop(big, (-m - int(round(wx * scale)), -m - int(round(wy * scale))), scale)


def render_screenshot(out: str, force_holiday: set, force_night: bool, n: int | None = None,
                      style: str = "dark", bg: "Backdrop | None" = None, edge: str = "top"):
    w = ClockWindow({**demo_clocks(n), "style": style, "edge": edge}, force_holiday, force_night)
    w.liq.reset_shown()
    scale = 2
    bal = None
    col = next((i for i, s in enumerate(w.states) if s.holiday), -1)
    if col >= 0:
        bal = ui.Balloon()
        head, rest = ui.balloon_text(w.states[col])
        bal.set_content(head, rest, pointer_up=True)
    bw = max(w.width(), bal.width() if bal else 0)
    pad = 24
    W = bw + 2 * pad
    H = w.height() + (bal.height() + 2 if bal else 0) + 2 * pad
    img = QImage(W * scale, H * scale, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(scale)
    p = QPainter(img)
    wx = pad + (bw - w.width()) // 2
    wy = pad if edge != "bottom" else H - pad - w.height() - (bal.height() + 2 if bal else 0)
    if bg is not None:
        arr = bg.canvas(W, H, scale)
        bg.paint(p, arr, W, H)
        attach_backdrop(w, arr, wx, wy, scale, W, H)
    else:
        grad = QLinearGradient(0, 0, W, H)
        grad.setColorAt(0, QColor(92, 104, 122))
        grad.setColorAt(1, QColor(150, 140, 128))
        p.fillRect(0, 0, W, H, grad)
    p.save()
    p.translate(wx, wy)
    w.paint_all(p)
    p.restore()
    if bal:
        anchor = wx + w.column_rect(col).center().x()
        bx = int(anchor - bal.width() / 2)
        bx = max(0, min(bx, W - bal.width()))
        bal.tail_x = anchor - bx
        by = wy + w.pill_origin().y() + ui.PILL_H + 2 - ui.Balloon.SH
        bal.render(p, QPoint(bx, by), renderFlags=QWidget.RenderFlag.DrawChildren)
    p.end()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    dump(w.states)
    print("saved", out)


# ---------------------------------------------------------------- reveal frames
BACKDROP = None      # --bg for --frames (a Backdrop)
FX_DEMO: dict = {}   # --fx for --frames / --settings-shot


def fx_demo_settings() -> dict:
    return {glass_fx.FX_SETTING[k]: True for k, on in FX_DEMO.items() if on}


def render_pointer_shots(out_dir: str, bg: "Backdrop", n: int | None = None):
    """Pointer light stills: cursor left / centre / right, then a press (squish + ripple)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    w = ClockWindow({"style": "glass", "glass_pointer": True, **demo_clocks(n)})
    w.liq.reset_shown()
    pr = w.pill_rect()
    cy = pr.center().y()
    shots = [("0_none", None, None), ("1_left", pr.left() + 40, cy - 8),
             ("2_centre", pr.center().x(), cy + 4), ("3_right", pr.right() - 30, cy + 10)]
    cells = []
    scale, pad = 2, 16
    for name, x, y in shots:
        w.pointer.reset()
        if x is not None:
            w.pointer.pos, w.pointer.hover, w.pointer.hover_target = (x, y), 1.0, 1.0
        cells.append((name, _render_state(w, w.liq, scale, pad)))
    px, py = pr.left() + pr.width() * 0.62, cy + 6
    for k, age in enumerate((0.07, 0.16, 0.30)):
        w.pointer.reset()
        w.pointer.pos, w.pointer.hover, w.pointer.hover_target = (px, py), 1.0, 1.0
        w.pointer.press(px, py)
        w.pointer.step(age)                 # squish spring + ripple, 'age' s after the click
        cells.append((f"{4 + k}_press_{int(age * 1000)}ms", _render_state(w, w.liq, scale, pad)))
    for name, img in cells:
        img.save(str(out / f"{name}.png"))
    cw, ch = cells[0][1].width(), cells[0][1].height()
    lab = 34
    sheet = QImage(cw, len(cells) * (ch + lab), QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor(28, 28, 30))
    p = QPainter(sheet)
    p.setFont(ui.make_font(22, ui.QFont.Weight.DemiBold))
    for i, (name, img) in enumerate(cells):
        img.setDevicePixelRatio(1)
        p.setPen(QColor(230, 230, 230))
        p.drawText(QPointF(12, i * (ch + lab) + 25), name)
        p.drawImage(QPointF(0, i * (ch + lab) + lab), img)
    p.end()
    sheet.save(str(out / "contact.png"))
    print("pointer shots ->", out)


def _bg(p: QPainter, W: float, H: float):
    grad = QLinearGradient(0, 0, W, H)
    grad.setColorAt(0, QColor(150, 162, 178))
    grad.setColorAt(1, QColor(196, 186, 172))
    p.fillRect(QRect(0, 0, int(W) + 1, int(H) + 1), grad)


def _record(w, fps: float, hold_s: float = 0.6):
    """Simulate show -> hold -> hide at a fixed rate. Returns [(phase, t_ms, alpha-check)]
    lists of window-only ARGB frames, plus the max alpha found on the non-edge borders."""
    liq = w.liq
    dt = 1.0 / fps
    frames = []          # (phase, t_ms, QImage of the window at 1x... rendered later)
    liq.reset_hidden()
    liq.set_mode("show")
    t = 0.0
    snaps = []
    while True:
        snaps.append(("show", t))
        frames.append(_snapshot(liq))
        if liq.is_settled or t > 3.0:
            break
        w.step_model(liq, dt)
        t += dt
    show_end = t
    for _ in range(int(hold_s * fps)):
        t += dt
        snaps.append(("hold", t))
        frames.append(_snapshot(liq))
    liq.set_mode("hide")
    t0 = t
    while True:
        w.step_model(liq, dt)
        t += dt
        snaps.append(("hide", t - t0))
        frames.append(_snapshot(liq))
        if liq.is_hidden or t - t0 > 3.0:
            break
    hide_end = t - t0
    for _ in range(int(0.35 * fps)):
        t += dt
        snaps.append(("empty", t))
        frames.append(_snapshot(liq))
    return snaps, frames, show_end, hide_end


def _snapshot(liq):
    import copy
    return copy.copy(liq)


def _render_state(w, state, scale: float, pad: int, bg=True) -> QImage:
    saved = w.liq
    w.liq = state
    W = w.width() + 2 * pad
    H = w.height() + pad
    img = QImage(int(W * scale), int(H * scale), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(scale)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    wy = 0 if w.edge != "bottom" else pad
    if bg and BACKDROP is not None:
        arr = BACKDROP.canvas(W, H, scale)
        BACKDROP.paint(p, arr, W, H)
        attach_backdrop(w, arr, pad, wy, scale, W, H)
    elif bg:
        _bg(p, W, H)
    elif w.style == "glass" and BACKDROP is not None:      # clipping check: still glass
        attach_backdrop(w, BACKDROP.canvas(W, H, scale), pad, wy, scale, W, H)
    p.translate(pad, wy)
    w.paint_all(p)
    p.end()
    w.liq = saved
    return img


def _border_alpha(w, state) -> int:
    """Max alpha on the window's non-edge borders (should be 0: nothing clipped)."""
    img = _render_state(w, state, 1.0, 0, bg=False).convertToFormat(QImage.Format.Format_ARGB32)
    W, H = img.width(), img.height()
    far_row = H - 1 if w.edge != "bottom" else 0
    m = 0
    for x in range(W):
        m = max(m, img.pixelColor(x, far_row).alpha())
    for y in range(H):
        m = max(m, img.pixelColor(0, y).alpha(), img.pixelColor(W - 1, y).alpha())
    return m


def render_frames(out_dir: str, edge: str, force_holiday: set, force_night: bool,
                  n: int | None = None, anim: str = "liquid", style: str = "dark",
                  in_speed: float = 1.0):
    """FX_DEMO (from --fx) switches Glass effects on for the render."""
    import shutil
    import subprocess
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    w = ClockWindow({"edge": edge, "anim": anim, "style": style, "in_speed": in_speed,
                     **demo_clocks(n), **fx_demo_settings()}, force_holiday, force_night)
    w.edge = edge
    scale, pad = 2, 16

    # ---- 60 fps run: PNG picks, contact sheet, MP4, clipping check
    snaps, states, show_end, hide_end = _record(w, 60)
    border = max(_border_alpha(w, s) for s in states)
    show_idx = [i for i, (ph, _) in enumerate(snaps) if ph == "show"]
    hide_idx = [i for i, (ph, _) in enumerate(snaps) if ph == "hide"]
    pick = [show_idx[round(k * (len(show_idx) - 1) / 19)] for k in range(20)]
    pick += [hide_idx[round(k * (len(hide_idx) - 1) / 5)] for k in range(6)]
    for f in out.glob("*.png"):
        f.unlink()
    cells = []
    for n, i in enumerate(pick):
        ph, t = snaps[i]
        img = _render_state(w, states[i], scale, pad)
        img.save(str(out / f"{n:02d}_{ph}_{int(round(t * 1000)):04d}ms.png"))
        img.setDevicePixelRatio(1)
        cells.append((f"{ph} {int(round(t * 1000))} ms", img))

    cols = 3
    cw, ch = cells[0][1].width(), cells[0][1].height()
    lab = 34
    rows = math.ceil(len(cells) / cols)
    sheet = QImage(cols * cw, rows * (ch + lab), QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor(28, 28, 30))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(ui.make_font(22, ui.QFont.Weight.DemiBold))
    for n, (label, img) in enumerate(cells):
        x, y = (n % cols) * cw, (n // cols) * (ch + lab)
        p.setPen(QColor(230, 230, 230))
        p.drawText(QPointF(x + 12, y + 25), label)
        p.drawImage(QPointF(x, y + lab), img)
        p.setPen(QColor(28, 28, 30))
        p.drawRect(x, y + lab, cw, ch)
    p.end()
    sheet.save(str(out / "contact.png"))

    # ---- zoomed strip of the drip (every 2nd frame until the drop lands), for judging the neck
    drip = [i for i in show_idx if not states[i].landed][::2][:12]
    drip += [i for i in hide_idx if not states[i].landed][::2][:12]
    if getattr(getattr(w.liq, "inner", w.liq), "kind", "liquid") not in ("liquid", "liquid_sdf"):
        drip = []                       # the drip strip only means something for liquid
    z, cwz, chz = 4, 64, 60
    strip = QImage(cwz * z * 6, (chz * z + lab) * max(1, math.ceil(len(drip) / 6)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    strip.fill(QColor(28, 28, 30))
    p = QPainter(strip)
    p.setFont(ui.make_font(22, ui.QFont.Weight.DemiBold))
    for n, i in enumerate(drip):
        img = _render_state(w, states[i], z, pad)
        img.setDevicePixelRatio(1)
        cx = (pad + w.width() / 2 - cwz / 2) * z
        cy = 0 if w.edge != "bottom" else img.height() - chz * z
        x, y = (n % 6) * cwz * z, (n // 6) * (chz * z + lab)
        p.setPen(QColor(230, 230, 230))
        p.drawText(QPointF(x + 10, y + 25), f"{snaps[i][0]} {snaps[i][1] * 1000:.0f} ms")
        p.drawImage(QRectF(x, y + lab, cwz * z, chz * z), img, QRectF(cx, cy, cwz * z, chz * z))
    p.end()
    strip.save(str(out / "drip.png"))

    seq = out / "_seq"
    shutil.rmtree(seq, ignore_errors=True)
    seq.mkdir()
    for i, s in enumerate(states):
        _render_state(w, s, scale, pad).save(str(seq / f"f{i:04d}.png"))
    ffmpeg = shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe"
    if Path(ffmpeg).exists():
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", "60",
                        "-i", str(seq / "f%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-crf", "14", "-movflags", "+faststart", str(out / "reveal.mp4")], check=False)
    shutil.rmtree(seq, ignore_errors=True)

    # ---- GIF: browsers clamp sub-20 ms GIF delays, so simulate at 50 fps for real-time playback
    try:
        from PIL import Image
        g_snaps, g_states, _, _ = _record(w, 50)
        pil = []
        for s in g_states:
            q = _render_state(w, s, 1.5, pad).convertToFormat(QImage.Format.Format_RGBA8888)
            pil.append(Image.frombuffer("RGBA", (q.width(), q.height()), bytes(q.constBits()),
                                        "raw", "RGBA", q.bytesPerLine(), 1).convert("RGB"))
        pal = [im.quantize(colors=255, method=Image.Quantize.MEDIANCUT) for im in pil]
        pal[0].save(str(out / "reveal.gif"), save_all=True, append_images=pal[1:],
                    duration=20, loop=0, optimize=False, disposal=1)
    except ImportError:
        print("Pillow missing: no GIF")
    print(f"show {show_end * 1000:.0f} ms, hide {hide_end * 1000:.0f} ms, "
          f"border alpha max {border} (0 = no clipping), frames -> {out}")


def dump(states):
    for s in states:
        print(f"{s.city.code} {s.local:%Y-%m-%d %H:%M %Z}  {s.time_text:>5}  {s.phase:8s} "
              f"moon {s.moon_age * clockdata.SYNODIC:4.1f}d "
              f"({'waxing' if s.moon_age < .5 else 'waning'}) illum {s.moon_illum:.0%}  "
              f"holiday={s.holiday}")


# ---------------------------------------------------------------- crash log / quit reasons
log = crashlog.log
_quit_reason: str | None = None
_console_cb = None


def request_quit(reason: str):
    """Every intentional quit goes through here: exit code 0 tells the watchdog to stop."""
    global _quit_reason
    _quit_reason = _quit_reason or reason
    log.info("Quit requested: %s", reason)
    QApplication.instance().exit(0)


def install_quit_logging(app):
    import supervisor

    def session_end(*_):
        global _quit_reason
        if _quit_reason != "Windows session end":
            _quit_reason = "Windows session end"
            log.info("Windows session is ending (logoff / shutdown)")
        supervisor.signal_session_end()     # the watchdog must not restart us

    app.commitDataRequest.connect(session_end)
    app.aboutToQuit.connect(lambda: log.info("Quitting (%s)", _quit_reason or "no reason given"))

    # console runs: Ctrl+C / window close / logoff seen by a console process
    global _console_cb
    try:
        import ctypes
        from ctypes import wintypes
        names = {0: "Ctrl+C", 1: "Ctrl+Break", 2: "console closed", 5: "logoff", 6: "shutdown"}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def handler(ev):
            log.info("Console control event: %s", names.get(ev, ev))
            if ev in (5, 6):
                session_end()
            return False
        _console_cb = handler
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_cb, True)
    except Exception:
        pass


def schedule_crash_test(mode: str):
    """--crash-test: exercise the log and the watchdog's restart paths."""
    log.warning("Crash test armed: %s", mode)

    def fire():
        log.warning("Crash test firing: %s", mode)
        if mode == "segfault":
            import faulthandler
            faulthandler._sigsegv()                  # real access violation -> faults.log
        elif mode == "exit":
            os._exit(3)                              # abrupt non-zero exit
        elif mode == "quit":
            request_quit("crash-test quit")          # clean exit 0
        elif mode == "slot":
            raise RuntimeError("crash-test: exception inside a Qt slot (app keeps running)")
    QTimer.singleShot(2000, fire)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screenshot", metavar="PNG")
    ap.add_argument("--force-holiday", default="", help="comma list of codes, e.g. GE")
    ap.add_argument("--force-night", action="store_true")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--frames", metavar="DIR", help="render the liquid reveal to DIR")
    ap.add_argument("--edge", default="top", choices=("top", "bottom"))
    ap.add_argument("--anim", default="liquid", choices=anim_modes.ANIM_KINDS,
                    help="liquid_classic = the v2 path-drawn liquid (A/B only)")
    ap.add_argument("--in-speed", type=float, default=1.0, help="--frames: show speed (0.5-2)")
    ap.add_argument("--clocks", type=int, help="screenshot / frames with N demo clocks (1-8)")
    ap.add_argument("--settings-shot", metavar="PNG", help="render the settings dialog")
    ap.add_argument("--search", help="with --settings-shot: open the search with this text")
    ap.add_argument("--style", default="dark", choices=ui.STYLES, help="screenshot / frames look")
    ap.add_argument("--bg", metavar="IMG", help="with --screenshot / --frames: backdrop image (glass)")
    ap.add_argument("--fx", default="", help="glass effects for --frames / --settings-shot: "
                                             "comma list of materialize,merge,pointer")
    ap.add_argument("--pointer-shots", metavar="DIR", help="render pointer-light stills (needs --bg)")
    ap.add_argument("--crash-test", choices=("exception", "segfault", "exit", "quit", "slot"),
                    help="debug: crash (or quit) ~2 s after start to test the log / watchdog")
    a = ap.parse_args()
    if not (a.dump or a.screenshot or a.frames or a.settings_shot or a.pointer_shots):
        crashlog.setup("app")                # normal run: log + crash hooks before Qt starts
        crashlog.install_exception_hooks()
        crashlog.enable_faulthandler()
        crashlog.suppress_crash_dialogs()
    force_h = {c.strip().upper() for c in a.force_holiday.split(",") if c.strip()}

    if a.dump:
        dump(clockdata.compute(force_holiday=force_h, force_night=a.force_night))
        return 0

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if crashlog.log.handlers:
        crashlog.install_qt_message_handler()

    bg = Backdrop(a.bg) if a.bg else None
    global FX_DEMO, BACKDROP
    FX_DEMO = {k: k in {x.strip() for x in a.fx.split(",")} for k in glass_fx.FX_KEYS}
    if a.pointer_shots:
        BACKDROP = bg
        render_pointer_shots(a.pointer_shots, bg, a.clocks)
        return 0
    if a.screenshot:
        render_screenshot(a.screenshot, force_h, a.force_night, a.clocks, a.style, bg, a.edge)
        return 0
    if a.frames:
        BACKDROP = bg
        render_frames(a.frames, a.edge, force_h, a.force_night, a.clocks, a.anim, a.style,
                      clean_speed(a.in_speed))
        return 0
    if a.settings_shot:
        render_settings_shot(a.settings_shot, a.search, a.style)
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    import supervisor                    # named-mutex single instance (can't go stale)
    if not supervisor.single_instance("app"):
        log.info("Another WorldClock is already running; exiting")
        return 0
    try:
        (DATA_DIR / "worldclock.lock").unlink(missing_ok=True)   # 1.0.0's lock file
    except OSError:
        pass
    log.info("WorldClock %s started (%s, pid %d, Python %s, Qt %s); screens: %s", __version__,
             "frozen" if FROZEN else "source", os.getpid(), sys.version.split()[0],
             qVersion(), crashlog.describe_screens(app))
    crashlog.hook_screens(app)
    crashlog.hook_power()
    install_quit_logging(app)

    settings, first_run = load_settings()
    if first_run:
        try:
            set_autostart(True)
        except OSError:
            pass
    else:
        try:
            migrate_autostart()
        except OSError as e:
            log.warning("Autostart migration failed: %s", e)
    # migrate older files (and fill defaults for a fresh install)
    changed = first_run
    if "clocks" not in settings:
        settings["clocks"] = clockdata.clocks_from_settings(settings)
        changed = True
    if settings.get("anim") not in anim_modes.ANIM_KINDS:
        settings["anim"] = "liquid"
        changed = True
    if "hide_delay" not in settings:
        settings["hide_delay"] = LEAVE_MS / 1000
        changed = True
    if settings.get("style") not in ui.STYLES:          # v9: Dark (the original look) / Glass
        settings["style"] = "dark"
        changed = True
    if not isinstance(settings.get("in_speed"), (int, float)) or isinstance(settings.get("in_speed"), bool):
        settings["in_speed"] = 1.0                       # v11: In speed, 1x
        changed = True
    for k in glass_fx.FX_KEYS:                           # v10: Glass effects, all off
        key = glass_fx.FX_SETTING[k]
        if not isinstance(settings.get(key), bool):
            settings[key] = False
            changed = True
    if settings.get("monitors") not in ("one", "all"):
        settings["monitors"] = "one"
        changed = True
    if "x_frac" not in settings:                    # old pixel offset -> top, centred, primary
        settings.pop("x", None)
        settings.update(edge="top", x_frac=0.5, screen=QGuiApplication.primaryScreen().name())
        changed = True
    if changed:
        save_settings(settings)

    clocks = ClockSet(settings, force_h, a.force_night)

    tray = QSystemTrayIcon(ui.tray_icon(), app)
    tray.setToolTip(f"World Clock {__version__}")
    menu = QMenu()
    style_menu(menu)
    act_ver = QAction(f"WorldClock {__version__}", menu)
    act_ver.setEnabled(False)
    act_auto = QAction("Start with Windows", menu, checkable=True)
    act_auto.setChecked(autostart_enabled())
    act_auto.toggled.connect(set_autostart)
    act_pin = QAction("Pin open", menu, checkable=True)
    act_pin.toggled.connect(clocks.set_pinned)
    act_reset = QAction("Reset position", menu)
    act_reset.triggered.connect(lambda: clocks.reset_position(None))   # primary, top, centred
    act_quit = QAction("Quit", menu)
    act_quit.triggered.connect(lambda: request_quit("tray menu Quit"))
    act_settings = QAction("Settings…", menu)
    dlg_holder = {}

    def open_settings():
        dlg = dlg_holder.get("dlg")
        if dlg is None:
            w = clocks.first
            dlg = settings_ui.SettingsDialog(w.clocks, w.anim_kind, w.hide_delay, clocks.mode,
                                             style=w.style, fx=dict(w.fx), in_speed=w.in_speed)
            dlg.delay_changed.connect(clocks.apply_hide_delay)
            dlg.speed_changed.connect(clocks.apply_in_speed)
            dlg.style_changed.connect(clocks.apply_style)
            dlg.fx_changed.connect(clocks.apply_fx)
            dlg.changed.connect(clocks.apply_clocks)
            dlg.anim_changed.connect(clocks.apply_anim)
            dlg.monitors_changed.connect(clocks.set_monitors)
            # keep the clock out while editing so changes are visible, then restore
            dlg.closed.connect(lambda: (clocks.set_pinned(act_pin.isChecked()),
                                        dlg_holder.pop("dlg", None)))
            dlg.adjustSize()
            scr = QGuiApplication.screenAt(QCursor.pos()) or w.target_screen()
            g = scr.availableGeometry()
            dlg.move(g.center().x() - dlg.width() // 2, g.center().y() - dlg.height() // 2)
            dlg_holder["dlg"] = dlg
        clocks.set_pinned(True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    act_settings.triggered.connect(open_settings)
    menu.addAction(act_settings)
    menu.addSeparator()
    for act in (act_pin, act_reset):
        menu.addAction(act)
    menu.addSeparator()
    menu.addAction(act_auto)
    menu.addSeparator()
    menu.addAction(act_ver)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: clocks.show_for(5)
                           if r == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()

    # the pill's own right-click menu
    pill_menu = QMenu()
    style_menu(pill_menu)
    act_p_settings = QAction("Settings…", pill_menu)
    act_p_settings.triggered.connect(open_settings)
    act_p_reset = QAction("Reset to centre", pill_menu)
    act_p_reset.triggered.connect(clocks.reset_from_menu)
    pill_menu.addAction(act_p_settings)
    pill_menu.addAction(act_p_reset)
    pill_menu.addSeparator()
    pill_menu.addAction(act_pin)
    pill_menu.addSeparator()
    act_p_quit = QAction("Quit", pill_menu)
    act_p_quit.triggered.connect(lambda: request_quit("pill menu Quit"))
    pill_menu.addAction(act_p_quit)
    clocks.context_menu = pill_menu

    def hook_screen(s):
        s.geometryChanged.connect(lambda *_: QTimer.singleShot(0, clocks.sync_windows))
        s.availableGeometryChanged.connect(lambda *_: QTimer.singleShot(0, clocks.sync_windows))
    for s in app.screens():
        hook_screen(s)
    app.screenAdded.connect(lambda s: (hook_screen(s), QTimer.singleShot(0, clocks.sync_windows)))
    app.screenRemoved.connect(lambda s: QTimer.singleShot(0, clocks.sync_windows))

    clocks.start()
    if first_run:
        clocks.show_for(4)       # say hello once so the user knows where it lives
    if a.crash_test == "exception":
        raise RuntimeError("crash-test: uncaught exception in main")
    if a.crash_test:
        schedule_crash_test(a.crash_test)
    rc = app.exec()
    log.info("Event loop ended, exit code %s", rc)
    return rc


if __name__ == "__main__":
    try:
        rc = main()
    except SystemExit:
        raise
    except BaseException:
        # log it, then exit hard with 1: the frozen (windowed) build would otherwise show a
        # modal "Unhandled exception" box and the watchdog would wait on it forever
        sys.excepthook(*sys.exc_info())
        os._exit(1)
    sys.exit(rc)
