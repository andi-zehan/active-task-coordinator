@echo off
REM ============================================================
REM Flow - launcher
REM Activates the virtual environment, opens the browser, then
REM runs the local server. Closing this window stops the app.
REM ============================================================
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Flow has not been set up yet.
    echo.
    echo Please double-click setup.bat first.
    echo.
    pause
    exit /b 1
)

echo Starting Flow on http://localhost:8080 ...
start "" "http://localhost:8080"

".venv\Scripts\python.exe" server.py

REM If the server exits, keep the window open so the user can read errors.
echo.
echo Flow has stopped.
pause
endlocal
