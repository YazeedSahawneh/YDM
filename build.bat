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
    --collect-submodules yt_dlp --collect-submodules spnego --hidden-import socksio main.py || goto :fail
echo.
echo Done: dist\YDM\YDM.exe

REM ---- installer (needs Inno Setup:  winget install -e --id JRSoftware.InnoSetup) ----
set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if not defined ISCC if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
    echo Inno Setup not found, so no installer was made.
    echo Install it with:  winget install -e --id JRSoftware.InnoSetup
    pause
    exit /b 0
)
for /f %%V in ('.venv\Scripts\python.exe -c "from ydm.config import APP_VERSION; print(APP_VERSION)"') do set "VER=%%V"
"%ISCC%" /Q /DAppVersion=%VER% installer\YDM.iss || goto :fail
echo Done: dist\YDM-Setup-%VER%.exe
pause
exit /b 0

:fail
echo Build failed.
pause
exit /b 1
