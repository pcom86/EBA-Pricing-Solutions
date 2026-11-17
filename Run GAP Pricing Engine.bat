@echo off
REM ============================================================================
REM  Run GAP Pricing Engine  (PySide6 app launcher)
REM  Keep this .bat in the repository ROOT (next to BackendEngine\).
REM  Uses the repo virtual environment at .venv\ when present.
REM  This version keeps a console window open so any error is visible.
REM  For a "no console" launch, use the silent .vbs instead.
REM ============================================================================

setlocal

REM --- The app logs Unicode characters (checkmarks/timers); keep the console ---
REM --- on UTF-8 so logging doesn't hit cp1252 UnicodeEncodeError noise. ---
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul

REM --- Run from the engine root (contains gap_pricing_gui, gap_config, etc.) ---
pushd "%~dp0BackendEngine"

REM --- Pick a Python interpreter ---
REM 1) honour an explicit override if you set GAP_PYTHON
REM 2) else use the repo virtual environment .venv\Scripts\python.exe
REM 3) else use the Windows launcher 'py' if present
REM 4) else fall back to plain 'python' on PATH
set "PYEXE=%GAP_PYTHON%"
if not defined PYEXE (
    if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
)
if not defined PYEXE (
    where py >nul 2>nul && set "PYEXE=py"
)
if not defined PYEXE set "PYEXE=python"

echo Launching GAP Pricing Engine with: %PYEXE%
echo Working dir: "%CD%"
echo.

REM ============================================================================
REM  Ensure required Python packages are installed (self-healing dependency check)
REM ============================================================================
echo Checking Python dependencies...
"%PYEXE%" "%~dp0BackendEngine\gap_pricing_gui\ensure_requirements.py"
if not "%ERRORLEVEL%"=="0" (
    echo.
    echo *** Dependency check/install failed - aborting launch. ***
    popd
    pause
    exit /b 1
)
echo.

"%PYEXE%" -B -m gap_pricing_gui.launch_gap_gui
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo *** GAP Pricing Engine exited with code %RC% ***
    pause
)

popd
endlocal
