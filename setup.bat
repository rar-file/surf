@echo off
setlocal enabledelayedexpansion

:: ============================================================
::  SURF - Quick Setup Script (Windows Batch)
::  Usage: Double-click setup.bat  OR  run from cmd/terminal
:: ============================================================

title SURF Setup

echo.
echo  ============================================================
echo   SURF - Search . Understand . Reason . Fast
echo   Windows Setup
echo  ============================================================
echo.

:: ── Check Python ────────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found.
    echo.
    echo  Please install Python 3.10+ from https://python.org
    echo  Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo  [OK] Found %PY_VER%
echo.

:: ── Create virtual environment ──────────────────────────────
if exist venv\ (
    echo  [OK] Virtual environment already exists, skipping creation.
) else (
    echo  [....] Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  [OK] Virtual environment created.
)
echo.

:: ── Activate and install dependencies ───────────────────────
echo  [....] Installing dependencies...
call venv\Scripts\activate.bat

python -m pip install --upgrade pip --quiet
if %errorlevel% neq 0 (
    echo  [ERROR] pip upgrade failed.
    pause
    exit /b 1
)

python -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo  [ERROR] Dependency install failed.
    echo         Check requirements.txt and your internet connection.
    pause
    exit /b 1
)
echo  [OK] Dependencies installed.
echo.

:: ── Install Playwright / Chromium ───────────────────────────
echo  [....] Installing Chromium (used for web search + browser agent)...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo  [WARN] Playwright browser install failed.
    echo        Web search and browser agent may not work.
    echo        Re-run manually:  python -m playwright install chromium
)
echo.

:: ── Done ────────────────────────────────────────────────────
echo  ============================================================
echo   Setup Complete!
echo  ============================================================
echo.
echo  To start SURF, run:
echo.
echo    start.bat             (launches the web UI at http://localhost:7777)
echo    start.bat cli         (launches the terminal CLI)
echo    start.bat search      (CLI with web search enabled)
echo.
echo  Or activate the environment manually and use:
echo    venv\Scripts\activate
echo    python chat.py --web
echo.
pause
endlocal
