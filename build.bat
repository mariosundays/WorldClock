@echo off
setlocal
rem Build WorldClock: PyInstaller onedir -> zip -> Inno Setup installer (if installed).
rem Run from a venv with:  pip install -r requirements-build.txt

for /f "delims=" %%v in ('python -c "from version import __version__ as v; print(v)"') do set VER=%%v
if not defined VER (
    echo Could not read version.py
    exit /b 1
)
if not exist releases mkdir releases

echo [1/3] Building with PyInstaller...
pyinstaller WorldClock.spec --clean -y
if errorlevel 1 (
    echo PyInstaller failed.
    exit /b 1
)

echo [2/3] Zipping for distribution...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\WorldClock\*' -DestinationPath 'releases\WorldClock-%VER%-win64.zip' -Force"
if errorlevel 1 (
    echo Zip failed.
    exit /b 1
)

set ISCC=
for %%P in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do (
    if exist %%P set ISCC=%%P
)
if not defined ISCC (
    echo.
    echo  Inno Setup not found: zip-only build complete.
    echo  Zip: releases\WorldClock-%VER%-win64.zip
    echo  Get Inno Setup from https://jrsoftware.org/isdl.php to build the installer.
    goto :done
)

echo [3/3] Compiling installer...
%ISCC% /DMyAppVersion=%VER% installer.iss
if errorlevel 1 (
    echo Inno Setup compilation failed.
    exit /b 1
)

:done
echo.
echo Done!
echo  Zip:       releases\WorldClock-%VER%-win64.zip
echo  Installer: installer_out\WorldClock-Setup-%VER%.exe
