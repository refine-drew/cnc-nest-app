@echo off
title CNC Nest Tool
cd /d "%~dp0"
echo ===================================
echo   CNC Nest Tool
echo ===================================
echo.

REM Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3 is not installed.
    echo.
    echo Please download and install it from:
    echo   https://python.org
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM Check / install dependencies (Flask, reportlab, etc.)
python -c "import flask, reportlab" >nul 2>&1
if errorlevel 1 (
    echo Dependencies not found. Installing...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to install dependencies.
        echo Try running: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo.
)

REM Open browser after server starts
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:5001"

echo Starting server...
echo.
echo CNC Nest Tool is running at http://localhost:5001
echo Close this window to stop the server.
echo.

python app.py
if errorlevel 1 (
    echo.
    echo ERROR: Server stopped unexpectedly. Check the output above for details.
    pause
)
