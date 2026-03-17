@echo off
setlocal enabledelayedexpansion

:: ============================================================
::  SURF - Launcher (Windows Batch)
::  Usage:
::    start.bat           -> Web UI (http://localhost:7777)
::    start.bat cli       -> Terminal CLI
::    start.bat search    -> CLI with web search on
:: ============================================================

title SURF

:: ── Check venv exists ────────────────────────────────────────
if not exist venv\Scripts\activate.bat (
    echo.
    echo  [ERROR] Virtual environment not found.
    echo         Run setup.bat first to install SURF.
    echo.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

:: ── Parse arguments ──────────────────────────────────────────
set MODE=%1

if /i "%MODE%"=="cli" (
    echo.
    echo  Launching SURF CLI...
    echo  Type /help for commands, /quit to exit.
    echo.
    python chat.py
    goto :end
)

if /i "%MODE%"=="search" (
    echo.
    echo  Launching SURF CLI with web search enabled...
    echo.
    python chat.py --search
    goto :end
)

:: Default: launch web UI via Python directly
echo.
echo  ============================================================
echo   SURF - Search . Understand . Reason . Fast
echo  ============================================================
echo.
echo  Launching Web UI at http://localhost:7777
echo  Press Ctrl+C to stop the server.
echo.
python -c "from core.web_ui import launch; launch(open_browser=True)"

:end
endlocal

