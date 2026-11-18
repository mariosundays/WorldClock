# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir build:  pyinstaller WorldClock.spec --clean -y
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                 StringTable, VarFileInfo, VarStruct,
                                                 VSVersionInfo)

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "version.py").read_text()).group(1)
vt = tuple(int(x) for x in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=vt, prodvers=vt),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Mario Domingos"),
            StringStruct("FileDescription", "WorldClock"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "WorldClock"),
            StringStruct("LegalCopyright", "GPL-3.0, Mario Domingos"),
            StringStruct("OriginalFilename", "WorldClock.exe"),
            StringStruct("ProductName", "WorldClock"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

# Qt modules / heavy packages WorldClock never touches.
EXCLUDES = [
    "PIL", "tkinter", "unittest", "setuptools", "pkg_resources", "pydoc", "matplotlib", "scipy", "pandas", "IPython",
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtSvg", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtDBus", "PySide6.QtConcurrent",
    "PySide6.QtPrintSupport", "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtHelp",
]

a = Analysis(
    ["worldclock.pyw"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[("cities.json", ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

# Drop what PyInstaller pulls in but the app never loads.
_DROP = (
    "opencv_videoio_ffmpeg",          # video I/O (glass only uses image ops)
    "qt6network", "qt6qml", "qt6quick", "qt6pdf", "qt6svg", "qt6opengl", "qt6virtualkeyboard",
    "opengl32sw",                     # software OpenGL fallback (~20 MB), not used by widgets
    "d3dcompiler",
)


def _keep(entry):
    name = entry[0].replace("\\", "/").lower()
    base = name.rsplit("/", 1)[-1]
    if any(base.startswith(d) for d in _DROP):
        return False
    if "/translations/" in name and not name.endswith(".qm"):
        return True
    if "/translations/" in name:      # Qt's own UI translations: the app is English-only
        return False
    if "/plugins/" in name:
        keep = ("/platforms/", "/styles/", "/imageformats/qico", "/iconengines/")
        return any(k in name for k in keep)
    return True


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WorldClock",
    icon="icon.ico",
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WorldClock",
)
