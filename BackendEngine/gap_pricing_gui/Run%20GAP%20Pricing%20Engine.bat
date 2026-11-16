@echo off
REM ============================================================================
REM  Run GAP Pricing Engine  (PySide6 app launcher)
REM  Keep this .bat in the SAME folder as launch_gap_gui.py and
REM  ensure_requirements.py (and, ideally, a requirements.txt).
REM  This version keeps a console window open so any error is visible.
REM  For a "no console" launch, use the silent .vbs instead.
REM ============================================================================

setlocal

REM --- Run from the folder this .bat lives in ---
REM  'cd /d' CANNOT handle UNC (\\server\share) paths and falls back to C:\Windows.
REM  'pushd' auto-maps a temporary drive letter to the UNC path, which CMD supports.
pushd "%~dp0"

REM --- Pick a Python interpreter ---
REM 1) honour an explicit override if you set GAP_PYTHON
REM 2) else use the Windows launcher 'py' if present
REM 3) else fall back to plain 'python' on PATH
set "PYEXE=%GAP_PYTHON%"
if not defined PYEXE (
    where py >nul 2>nul && set "PYEXE=py"
)
if not defined PYEXE set "PYEXE=python"

echo Launching GAP Pricing Engine with: %PYEXE%
REM  Quote %CD% -- the path contains '&' ("Product & Client Solutions"),
REM  which CMD treats as a command separator when unquoted.
echo Working dir: "%CD%"
echo.

REM ============================================================================
REM  Ensure required Python packages are installed (self-healing dependency check)
REM  Logic lives in ensure_requirements.py so it's easy to read/edit/test on its
REM  own, rather than embedding multi-line Python inside this batch file.
REM ============================================================================
echo Checking Python dependencies...
"%PYEXE%" "%~dp0ensure_requirements.py"
if not "%ERRORLEVEL%"=="0" (
    echo.
    echo *** Dependency check/install failed - aborting launch. ***
    popd
    pause
    exit /b 1
)
echo.

popd
pushd "%~dp0.."
"%PYEXE%" -m gap_pricing_gui.launch_gap_gui
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo *** GAP Pricing Engine exited with code %RC% ***
    echo See launch_gap_gui.log for details.
    pause
)

REM --- Release the temporary UNC drive mapping created by pushd ---
popd

endlocal
