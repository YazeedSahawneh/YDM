@echo off
setlocal
cd /d "%~dp0"

REM ---- find a real Python (the "py" launcher, or python.exe on PATH) ----
set "PY="
where py >nul 2>&1 && py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo  Python was not found on this PC.
    echo.
    echo  Install it with ONE of these, then run this file again:
    echo    1^) In this window:   winget install -e --id Python.Python.3.12
    echo    2^) Or download from https://www.python.org/downloads/
    echo       and TICK "Add python.exe to PATH" on the first screen.
    echo.
    echo  After installing, close this window and open run.bat again.
    echo.
    pause
    exit /b 1
)

REM ---- first run: create the virtual environment and install packages ----
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment with %PY% ...
    if exist .venv rmdir /s /q .venv
    %PY% -m venv .venv || goto :fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
    echo.
    echo Setup finished.
)

start "" ".venv\Scripts\pythonw.exe" main.py %*
exit /b 0

:fail
echo.
echo  Setup failed. Check your internet connection, then run this file again.
pause
exit /b 1
