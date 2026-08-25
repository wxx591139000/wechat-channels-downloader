@echo off
setlocal
cd /d %~dp0
title sphdl - WeChat Channels Video Downloader

rem ===== 1. Find a working venv (prefer local .venv, then project root ..\.venv) =====
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"

rem ===== 2. Create venv only if missing (try py launcher first, then python) =====
if not defined PY (
    echo [First run] No virtualenv found. Creating .venv and installing deps - this may take a few minutes...
    py -3 -m venv .venv 2>nul
    if errorlevel 1 python -m venv .venv
    if errorlevel 1 (
        echo.
        echo [ERROR] Python not found on this machine.
        echo Install Python 3.9+ from https://www.python.org/downloads/
        echo and CHECK "Add Python to PATH" during install, then double-click this file again.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency installation failed. Check your network and retry.
        pause
        exit /b 1
    )
    set "PY=.venv\Scripts\python.exe"
)

rem ===== 3. Run (always pause at the end so the window never flashes away) =====
echo [Run] Using: %PY%
echo [Stop] In GUI press the red X or Ctrl+C. System proxy is restored automatically.
echo.
if "%*"=="" (
    rem no args -> GUI mode (recommended)
    "%PY%" main.py --gui
) else (
    rem args given -> CLI mode passthrough
    "%PY%" main.py %*
)
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
    echo [Done] Exited normally.
) else (
    echo [Note] Exit code %RC%. Ctrl+C interruption is normal.
)
echo.
pause
endlocal
