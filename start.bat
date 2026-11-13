@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title PodFetch - YouTube downloader
echo ========================================
echo   PodFetch — YouTube downloader
echo ========================================
echo.

set "PY="
where py >nul 2>&1 && (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1 && (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "PY=python"
  )
)
if not defined PY (
  where python3 >nul 2>&1 && (
    python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "PY=python3"
  )
)
if not defined PY (
  echo Python 3.10 or newer is required, but it was not found on this computer.
  echo.
  echo Install it from https://www.python.org/downloads/
  echo During setup, tick "Add python.exe to PATH", then double-click start.bat again.
  echo.
  pause
  exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo ffmpeg is required, but it was not found on PATH.
  echo.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo winget is not available, so ffmpeg cannot be installed automatically.
    echo Install ffmpeg, then close this window and double-click start.bat again:
    echo   - Chocolatey:  choco install ffmpeg
    echo   - Or download a build from https://ffmpeg.org/download.html
    echo     and add the "bin" folder to your PATH.
    echo.
    pause
    exit /b 1
  )
  echo Trying to install ffmpeg with winget...
  winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
  set "PATH=%PATH%;C:\ffmpeg\bin;C:\Program Files\ffmpeg\bin;C:\Program Files\Gyan\FFmpeg\bin"
  where ffmpeg >nul 2>&1
  if errorlevel 1 (
    echo.
    echo ffmpeg was installed, but this window cannot see it yet.
    echo Close this window and double-click start.bat again.
    echo.
    echo If it still is not found, install it from https://ffmpeg.org/download.html
    echo and add the "bin" folder to your PATH.
    echo.
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating a Python environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create the Python environment.
    pause
    exit /b 1
  )
)

echo Installing / updating packages...
".venv\Scripts\python.exe" -m pip install -U pip >nul
".venv\Scripts\python.exe" -m pip install -U -r requirements.txt
if errorlevel 1 (
  echo Could not install Python packages. Check your internet connection and try again.
  pause
  exit /b 1
)

set "PORT=5000"
set "URL=http://127.0.0.1:5000"
set "YT_SKIP_UPDATE=1"

".venv\Scripts\python.exe" -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=1)" >nul 2>&1
if not errorlevel 1 (
  echo The app is already running. Opening %URL%
  start "" "%URL%"
  pause
  exit /b 0
)

echo.
echo Starting the app at %URL%
echo Leave this window open while you use it. Close the window to stop.
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul & start "" %URL%"
".venv\Scripts\python.exe" app.py
echo.
echo The app has stopped.
pause
