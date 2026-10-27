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
    --additional-hooks-dir build_hooks ^
    --add-data "ui;ui" ^
    --add-data "assets\logo;assets\logo" ^
    --add-data "LICENSE;." ^
    --add-data "LICENSES\NOTICE_libpff.txt;LICENSES" ^
    --add-data "LICENSES\NOTICE_py7zr.txt;LICENSES" ^
    --add-data "THIRD_PARTY_NOTICES.md;." ^
    --collect-all pypff ^
    --collect-all pypdfium2 ^
    --collect-all pypdfium2_raw ^
    --collect-all nh3 ^
    --collect-all py7zr ^
    --collect-all pyppmd ^
    --collect-all pybcj ^
    --collect-all inflate64 ^
    run_mailscope.py
if errorlevel 1 exit /b 1

echo Build complete: dist\Mail-Scope\Mail-Scope.exe

rem Build the installer when Inno Setup 6 or 7 is available (INNO_SETUP_COMPILER may point to ISCC.exe).
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if defined INNO_SETUP_COMPILER set "ISCC=%INNO_SETUP_COMPILER%"
if not defined ISCC goto no_installer

set "VERSION="
for /f "usebackq delims=" %%v in (`.venv\Scripts\python.exe -c "from version import __version__; print(__version__)"`) do set "VERSION=%%v"
if not defined VERSION (
    echo The application version could not be read from version.py.
    exit /b 1
)
call "%ISCC%" /Qp /DAppVersion=%VERSION% installer\Mail-Scope.iss
if errorlevel 1 exit /b 1
echo Installer complete: dist\Mail-Scope-Setup-%VERSION%-x64.exe
exit /b 0

:no_installer
echo Inno Setup 6 or 7 was not found, so the installer was not built; the application is in dist\Mail-Scope.
exit /b 0
