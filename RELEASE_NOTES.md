**WorldClock 1.0.1**: crash log and auto-restart.

- **Auto-restart:** Start with Windows and the Start Menu shortcut now launch `WorldClock.exe --watchdog`, a tiny supervisor (no Qt loaded) that restarts WorldClock if it crashes or is killed (2 s, then backing off; at most 5 restarts in 10 minutes). Quit from the menu, or a Windows logoff/shutdown, stops it for good. Existing installs update their autostart entry on first run.
- **Crash log:** `%APPDATA%\WorldClock\logs\worldclock.log` (rotating, 1 MB x 3) records starts, quits and why, screen changes, sleep/resume, Python and Qt errors, and every restart. Hard crashes leave a traceback in `faults.log` next to it.
- The single-instance lock can no longer get stuck after a crash.

**Install:** download `WorldClock-Setup-1.0.1.exe` and run it (per-user, no admin); settings are kept. Prefer no installer? Unzip `WorldClock-1.0.1-win64.zip` and run `WorldClock.exe --watchdog`.

Windows 10/11, 64-bit. The installer isn't code-signed, so SmartScreen may ask you to confirm ("More info" > "Run anyway").
