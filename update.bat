@echo off
title CNC Nest Tool — Update
cd /d "%~dp0"
echo ===================================
echo   CNC Nest Tool — Update
echo ===================================
echo.

REM Check git is installed
git --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed.
    echo.
    echo Please download and install it from:
    echo   https://git-scm.com
    echo.
    pause
    exit /b 1
)

echo Pulling latest updates from GitHub...
git pull

if errorlevel 1 (
    echo.
    echo ERROR: Update failed. Check your internet connection or the output above.
    pause
    exit /b 1
)

echo.
echo Update successful! Relaunching app...
echo.

call "%~dp0launch.bat"
