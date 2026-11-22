"""WorldClock watchdog: `WorldClock.exe --watchdog` (autostart + Start Menu shortcut).

Runs before PySide6 / numpy / cv2 are imported (stdlib + ctypes only, a few MB), starts
the real app as a child and waits on it:
  exit code 0      intentional quit (tray / menu Quit, Windows logoff) -> watchdog exits too
  anything else    crash, kill, error -> logged, restarted after 2 s with backoff
                   (2, 4, 8, 16, 30 s), at most MAX_RESTARTS in WINDOW_S, then it gives up
A Windows session end (logoff / shutdown) never triggers a restart: the app signals a
named event before it goes, and the watchdog also checks SM_SHUTTINGDOWN.

Single instance: named mutexes (released by Windows when a process dies, so a crash can
never leave a stale lock). A second watchdog or app exits quietly with 0.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import crashlog
from version import __version__

MAX_RESTARTS = 5
WINDOW_S = 600
FIRST_DELAY_S = 2.0
MAX_DELAY_S = 30.0

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_k32.SetEvent.argtypes = (wintypes.HANDLE,)
_k32.ResetEvent.argtypes = (wintypes.HANDLE,)
ERROR_ALREADY_EXISTS = 183
_held = []          # handles kept for the process lifetime


def _name(kind: str) -> str:
    return f"Local\\WorldClock-{kind}-{crashlog.instance_tag()}"


def single_instance(kind: str) -> bool:
    """Take the named mutex for 'app' or 'watchdog'. False if another process holds it."""
    h = _k32.CreateMutexW(None, False, _name(kind))
    err = ctypes.get_last_error()
    if not h:
        return True                       # can't tell: don't block startup
    if err == ERROR_ALREADY_EXISTS:
        _k32.CloseHandle(h)
        return False
    _held.append(h)
    return True


def _session_event():
    """Manual-reset event the app sets when Windows ends the session."""
    h = _k32.CreateEventW(None, True, False, _name("session-end"))
    if h:
        _held.append(h)
    return h


def signal_session_end():
    """Called by the app on logoff / shutdown so its watchdog won't restart it."""
    h = _session_event()
    if h:
        _k32.SetEvent(h)


def session_ending(ev) -> bool:
    SM_SHUTTINGDOWN = 0x2000
    try:
        if ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN):
            return True
    except Exception:
        pass
    return bool(ev) and _k32.WaitForSingleObject(ev, 0) == 0


def child_command(extra: list[str]) -> list[str]:
    if crashlog.FROZEN:
        return [sys.executable, *extra]
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")                  # no console for the child
    script = Path(__file__).resolve().with_name("worldclock.pyw")
    return [str(pyw if pyw.exists() else exe), str(script), *extra]


def _describe(rc: int) -> str:
    u = rc & 0xFFFFFFFF
    known = {0xC0000005: "access violation", 0xC00000FD: "stack overflow",
             0xC0000409: "stack buffer overrun / fail-fast", 0xC0000374: "heap corruption",
             0xC000013A: "Ctrl+C / console closed", 0x40010004: "killed (debugger/terminated)"}
    if u in known:
        return f"0x{u:08X} {known[u]}"
    if u >= 0x80000000:
        return f"0x{u:08X}"
    return str(rc) + (" (Python error, or killed with taskkill /F)" if rc == 1 else "")


def _split_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """(args for the first child, args for later children). --crash-test only reaches the
    first child unless --crash-test-repeat is given (used to test the backoff cap)."""
    args = [a for a in argv if a != "--watchdog"]
    repeat = "--crash-test-repeat" in args
    args = [a for a in args if a != "--crash-test-repeat"]
    later, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--crash-test":
            skip = True
            continue
        if a.startswith("--crash-test="):
            continue
        later.append(a)
    return args, (args if repeat else later)


def run(argv: list[str]) -> int:
    log = crashlog.setup("watchdog")
    crashlog.install_exception_hooks()
    if not single_instance("watchdog"):
        log.info("Another watchdog is already running; exiting")
        return 0
    try:
        stale = crashlog.DATA_DIR / "worldclock.lock"   # 1.0.0's QLockFile, no longer used
        if stale.exists():
            stale.unlink()
    except OSError:
        pass
    ev = _session_event()
    if ev:
        _k32.ResetEvent(ev)
    first_args, later_args = _split_args(argv)
    log.info("Watchdog %s started (%s, pid %d)", __version__,
             "frozen" if crashlog.FROZEN else "source", os.getpid())

    restarts: list[float] = []
    args = first_args
    while True:
        cmd = child_command(args)
        started = time.monotonic()
        try:
            p = subprocess.Popen(cmd, close_fds=True,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            log.error("Could not start the app (%s): %s", cmd, e)
            return 1
        log.info("Started app pid %d", p.pid)
        rc = p.wait()
        ran = time.monotonic() - started
        if rc == 0:
            log.info("App pid %d exited cleanly (0) after %.0f s; watchdog exiting", p.pid, ran)
            return 0
        if session_ending(ev):
            log.info("App pid %d ended with %s during Windows session end; not restarting",
                     p.pid, _describe(rc))
            return 0
        now = time.monotonic()
        restarts = [t for t in restarts if now - t < WINDOW_S]
        if len(restarts) >= MAX_RESTARTS:
            log.error("App pid %d exited with %s after %.0f s. %d restarts in the last %d min: "
                      "giving up", p.pid, _describe(rc), ran, len(restarts), WINDOW_S // 60)
            return 2
        delay = min(MAX_DELAY_S, FIRST_DELAY_S * 2 ** len(restarts))
        log.warning("App pid %d exited with %s after %.0f s; restart %d/%d in %.0f s",
                    p.pid, _describe(rc), ran, len(restarts) + 1, MAX_RESTARTS, delay)
        time.sleep(delay)
        if session_ending(ev):
            log.info("Windows session is ending; not restarting")
            return 0
        restarts.append(time.monotonic())
        args = later_args
