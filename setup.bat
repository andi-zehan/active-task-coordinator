@echo off
REM ============================================================
REM Flow - first-time setup
REM Detects Python 3.10+, creates a virtual environment, installs
REM the dependencies. Run this once after extracting the zip.
REM ============================================================
setlocal

cd /d "%~dp0"

echo.
echo ============================================
echo  Flow - first-time setup
echo ============================================
echo.

REM ---- Locate Python -----------------------------------------
set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3"
)
if not defined PY_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python is not installed.
    echo.
    echo Please install Python 3.10 or newer from:
    echo     https://www.python.org/downloads/
    echo.
    echo IMPORTANT: during installation, tick "Add Python to PATH".
    echo Then double-click setup.bat again.
    echo.
    pause
    exit /b 1
)

REM ---- Verify version ----------------------------------------
%PY_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is too old. Need 3.10 or newer.
    %PY_CMD% --version
    echo.
    echo Please install a current version from:
    echo     https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo Found:
%PY_CMD% --version
echo.

REM ---- Create venv -------------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo Virtual environment already exists - reusing it.
) else (
    echo Creating virtual environment in .venv ...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)
echo.

REM ---- Install dependencies ----------------------------------
echo Installing dependencies (this can take a minute) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. See messages above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Setup complete.
echo  Double-click start.bat to launch Flow.
echo ============================================
echo.
pause
endlocal
