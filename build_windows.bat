@echo off
setlocal

rem Build the Windows x64 application with an isolated Python 3.13 environment.
py -3.13 -c "import struct,sys; raise SystemExit(0 if struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
    echo Mail-Scope requires 64-bit Python 3.13.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    py -3.13 -m venv .venv
    if errorlevel 1 exit /b 1
)

call ".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

call ".venv\Scripts\python.exe" -m PyInstaller ^
    --clean ^
    --noconfirm ^
    --onedir ^
    --noconsole ^
    --name Mail-Scope ^
    --icon "assets\logo\mail-scope.ico" ^
    --add-data "ui;ui" ^
    --add-data "assets\logo;assets\logo" ^
    --add-data "LICENSES\NOTICE_libpff.txt;LICENSES" ^
    --add-data "THIRD_PARTY_NOTICES.md;." ^
    --collect-all pypff ^
    run_mailscope.py
if errorlevel 1 exit /b 1

echo Build complete: dist\Mail-Scope\Mail-Scope.exe
