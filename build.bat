@echo off
REM Builds a standalone dist\YDM\YDM.exe (no Python needed on the target PC).
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python was not found. Install it first:  winget install -e --id Python.Python.3.12
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    if exist .venv rmdir /s /q .venv
    %PY% -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :fail
".venv\Scripts\pyinstaller.exe" --noconfirm --windowed --name YDM --icon assets\ydm.ico ^
    --collect-submodules yt_dlp main.py || goto :fail
echo.
echo Done: dist\YDM\YDM.exe
pause
exit /b 0

:fail
echo Build failed.
pause
exit /b 1
