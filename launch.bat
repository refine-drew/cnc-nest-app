@echo off
title Refine SS2 Layout Tool
cd /d "%~dp0"
echo ===================================
echo   Refine SS2 Layout Tool
echo ===================================
echo.

REM Update first, so a daily launch is a daily update (decided 2026-10-08). Offline,
REM git missing, or a local edit in the way: say so and start the version that is here.
REM GIT_TERMINAL_PROMPT=0 makes a credential prompt fail instead of hang; the repo is
REM public, so none is expected.
set GIT_TERMINAL_PROMPT=0
set "BEFORE="
set "AFTER="
git --version >nul 2>&1
if errorlevel 1 (
    echo Git is not installed, so updates cannot be fetched. Starting the version you have.
    echo.
) else (
    for /f %%i in ('git rev-parse HEAD 2^>nul') do set "BEFORE=%%i"
    echo Checking for updates...
    git pull --ff-only
    if errorlevel 1 (
        echo.
        echo Could not update ^(offline, or a file here was changed by hand^).
        echo Starting the version you have.
        echo.
    )
    for /f %%i in ('git rev-parse HEAD 2^>nul') do set "AFTER=%%i"
)

REM A .bat cannot carry an icon, so the RK icon lives on a desktop shortcut to this
REM launcher, made if it is missing. Sits after the pull so a pull that rewrites this
REM file mid-run does not move anything above where cmd resumes reading.
set "SHORTCUT_PS=$l=Join-Path ([Environment]::GetFolderPath('Desktop')) 'SS2 Layout Tool.lnk'; if (-not (Test-Path $l)) { $s=(New-Object -ComObject WScript.Shell).CreateShortcut($l); $s.TargetPath='%~dp0launch.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0icon\rk.ico,0'; $s.Save() }"
powershell -NoProfile -ExecutionPolicy Bypass -Command "%SHORTCUT_PS%" >nul 2>&1

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

REM Run from the project's own virtual environment, so the app never depends
REM on which Python is first on PATH or on installing into it.
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

REM Check / install dependencies (Flask, reportlab, etc.). Also re-run after an
REM update, in case requirements.txt changed.
set "NEED_PIP="
"%PYTHON%" -c "import flask, reportlab" >nul 2>&1
if errorlevel 1 set "NEED_PIP=1"
if defined BEFORE if defined AFTER if not "%BEFORE%"=="%AFTER%" set "NEED_PIP=1"
if defined NEED_PIP (
    echo Installing dependencies...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to install dependencies.
        echo Try running: .venv\Scripts\python.exe -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo.
)

REM Open browser after server starts
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:5001"

echo Starting server...
echo.
echo Refine SS2 Layout Tool is running at http://localhost:5001
echo Close this window to stop the server.
echo.

"%PYTHON%" app.py
if errorlevel 1 (
    echo.
    echo ERROR: Server stopped unexpectedly. Check the output above for details.
    pause
)
