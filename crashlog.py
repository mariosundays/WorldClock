"""Crash / diagnostic log for WorldClock (stdlib only: the watchdog imports it before Qt).

%APPDATA%\\WorldClock\\logs\\worldclock.log   rotating text log (1 MB x 3), shared by the
                                            watchdog and the app (opened per record, so
                                            either process can rotate it)
%APPDATA%\\WorldClock\\logs\\faults.log       faulthandler output (hard crashes in Qt/OpenCV)

Nothing here logs per frame; repeated errors are rate-limited.
"""
from __future__ import annotations

import ctypes
import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
import zlib
from pathlib import Path

from version import __version__

APP_NAME = "WorldClock"
DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "worldclock.log"
FAULT_FILE = LOG_DIR / "faults.log"
FROZEN = getattr(sys, "frozen", False)

log = logging.getLogger("worldclock")
_fault_fh = None                     # faults.log stays open for the process lifetime
_seen: dict[str, int] = {}           # rate limit for repeated errors / Qt messages
_seen_lock = threading.Lock()


class _ShareableRotatingHandler(logging.handlers.RotatingFileHandler):
    """Rotating handler that closes the file after every record, so two processes
    (watchdog + app) can append to and rotate the same log on Windows."""

    def emit(self, record):
        try:
            super().emit(record)
        finally:
            if self.stream is not None:
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None

    def handleError(self, record):   # never let logging itself kill the app
        pass


def instance_tag() -> str:
    """Per-data-folder tag for kernel object names (mutexes, events), so a test run with
    a different APPDATA never collides with the installed copy."""
    return f"{zlib.crc32(str(DATA_DIR).lower().encode()):08x}"


def setup(role: str) -> logging.Logger:
    """Attach the rotating file handler. role = 'app' or 'watchdog' (shown on every line)."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return log
    if not any(isinstance(h, _ShareableRotatingHandler) for h in log.handlers):
        h = _ShareableRotatingHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3,
                                      encoding="utf-8", delay=True)
        h.setFormatter(logging.Formatter(
            f"%(asctime)s.%(msecs)03d [{role} %(process)d] %(levelname)s %(message)s",
            "%Y-%m-%d %H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def _first_seen(key: str, limit: int = 3) -> tuple[bool, int]:
    """True for the first `limit` occurrences of key, then every 500th."""
    with _seen_lock:
        n = _seen.get(key, 0) + 1
        _seen[key] = n
    return (n <= limit or n % 500 == 0), n


def _log_exc(where: str, et, ev, tb):
    text = "".join(traceback.format_exception(et, ev, tb))
    ok, n = _first_seen(where + text)
    if ok:
        suffix = f" (seen {n}x)" if n > 1 else ""
        log.error("%s%s:\n%s", where, suffix, text.rstrip())


def install_exception_hooks():
    """sys.excepthook (also what PySide calls for exceptions raised inside Qt slots and
    virtual overrides, which it then swallows) and threading.excepthook (glass grabber)."""
    prev = sys.excepthook

    def hook(et, ev, tb):
        if issubclass(et, KeyboardInterrupt):
            log.info("KeyboardInterrupt")
        else:
            _log_exc("Uncaught exception", et, ev, tb)
        try:
            if prev is not None and sys.stderr is not None:   # console runs still print it
                prev(et, ev, tb)
        except Exception:
            pass

    def thook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        _log_exc(f"Uncaught exception in thread {name}", args.exc_type, args.exc_value,
                 args.exc_traceback)

    sys.excepthook = hook
    threading.excepthook = thook


def enable_faulthandler():
    """Hard crashes (access violations in Qt / OpenCV) dump every thread's Python stack
    into faults.log. The file is opened once and kept open: faulthandler writes to the
    raw fd from inside the crash handler."""
    global _fault_fh
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _fault_fh = open(FAULT_FILE, "a", encoding="utf-8", buffering=1)
        _fault_fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} "
                        f"WorldClock {__version__} started\n")
        _fault_fh.flush()
        faulthandler.enable(file=_fault_fh, all_threads=True)
    except Exception as e:
        log.warning("faulthandler not enabled: %s", e)


def suppress_crash_dialogs():
    """No WER 'has stopped working' box: it would keep a crashed child alive and stall the
    watchdog. The crash still lands in faults.log and the watchdog logs the exit code."""
    try:
        SEM_FAILCRITICALERRORS, SEM_NOGPFAULTERRORBOX = 0x0001, 0x0002
        k32 = ctypes.windll.kernel32
        k32.SetErrorMode(k32.GetErrorMode() | SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX)
    except Exception:
        pass


# ---------------------------------------------------------------- Qt (imported lazily)
def install_qt_message_handler():
    """Qt warnings, criticals and fatals go to the log (deduplicated)."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    levels = {QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}

    def handler(mode, ctx, msg):
        lvl = levels.get(mode)
        if lvl is None:
            return
        ok, n = _first_seen("qt" + msg)
        if ok:
            where = f" ({ctx.file}:{ctx.line})" if ctx is not None and ctx.file else ""
            log.log(lvl, "Qt: %s%s%s", msg, where, f" (seen {n}x)" if n > 1 else "")

    qInstallMessageHandler(handler)


def describe_screens(app) -> str:
    out = []
    for s in app.screens():
        g = s.geometry()
        out.append(f"{s.name()} {g.width()}x{g.height()}+{g.x()}+{g.y()} @{s.devicePixelRatio():g}")
    return "; ".join(out) or "none"


def hook_screens(app):
    def geo(s):
        g = s.geometry()
        return f"{s.name()} {g.width()}x{g.height()}+{g.x()}+{g.y()} @{s.devicePixelRatio():g}"

    def watch(s):
        s.geometryChanged.connect(lambda *_: log.info("Screen geometry changed: %s", geo(s)))

    for s in app.screens():
        watch(s)
    app.screenAdded.connect(lambda s: (log.info("Screen added: %s", geo(s)), watch(s)))
    app.screenRemoved.connect(lambda s: log.info("Screen removed: %s", s.name()))
    app.primaryScreenChanged.connect(lambda s: log.info("Primary screen is now %s", s.name()))


# ---------------------------------------------------------------- power (no Qt needed)
_power_cb = None   # keep the ctypes callback alive


def hook_power():
    """Suspend / resume via PowerRegisterSuspendResumeNotification (a system-thread
    callback): cheaper than a Qt native event filter, which would run Python per message."""
    global _power_cb
    try:
        from ctypes import wintypes
        CB = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p)
        names = {4: "suspend", 7: "resume (user)", 18: "resume (automatic)"}

        def cb(_ctx, typ, _setting):
            if typ in names:
                log.info("Power: %s", names[typ])
            return 0

        class PARAMS(ctypes.Structure):
            _fields_ = [("Callback", CB), ("Context", ctypes.c_void_p)]

        _power_cb = CB(cb)
        params = PARAMS(_power_cb, None)
        handle = ctypes.c_void_p()
        hook_power._params = params           # must outlive the registration
        DEVICE_NOTIFY_CALLBACK = 2
        rc = ctypes.windll.powrprof.PowerRegisterSuspendResumeNotification(
            DEVICE_NOTIFY_CALLBACK, ctypes.byref(params), ctypes.byref(handle))
        if rc != 0:
            log.info("Power notifications unavailable (rc %s)", rc)
    except Exception as e:
        log.info("Power notifications unavailable: %s", e)
