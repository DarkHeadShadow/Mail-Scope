@echo off
setlocal

rem Start Mail-Scope with the project's supported Python runtime.
py -3.13 -m server %*
if errorlevel 1 (
    echo Mail-Scope could not start. Verify that Python 3.13 and requirements.txt are installed.
    exit /b 1
)
