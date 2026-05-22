@echo off
title CNC Nest Tool
cd /d "%~dp0"
echo Starting CNC Nest Tool...
echo.

REM Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Please install Python from python.org
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)

REM Check Flask is installed
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo Installing Flask...
    python -m pip install flask
)

REM Pull latest from GitHub
git pull >nul 2>&1

REM Open browser after server starts
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5000"

echo.
echo CNC Nest Tool is running at http://localhost:5000
echo Close this window to stop the server.
echo.

python app.py
