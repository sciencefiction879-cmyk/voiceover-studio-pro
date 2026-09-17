@echo off
title Voiceover Studio Pro
cd /d "%~dp0"
echo ===================================================
echo   Starting Voiceover Studio Pro
echo ===================================================

:: 1. Check for compiled standalone binary first
if exist "VoiceoverStudio.exe" (
    echo Launching Voiceover Studio Pro Standalone...
    start "" "VoiceoverStudio.exe"
    exit /b 0
)

:: 2. Check for bundled portable python
if exist "python\python.exe" (
    set "PYTHON_EXE=%~dp0python\python.exe"
    goto :LAUNCH
)

:: 3. Check for system python or py launcher
where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_EXE=python"
    goto :CHECK_DEPS
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_EXE=py"
    goto :CHECK_DEPS
)

echo.
echo ===================================================
echo [ERROR] Python was not found on this system!
echo ===================================================
echo Please install Python 3.9 - 3.12 from:
echo   https://www.python.org/downloads/
echo.
echo *IMPORTANT*: Check "Add Python to PATH" during installation.
echo ===================================================
echo.
pause
exit /b 1

:CHECK_DEPS
echo Checking environment...
%PYTHON_EXE% -c "import flask, flask_cors, numpy, werkzeug" >nul 2>nul
if %errorlevel% neq 0 (
    echo Installing required dependencies (one-time setup)...
    %PYTHON_EXE% -m pip install -r requirements.txt
)

:LAUNCH
echo Launching Voiceover Studio Pro Desktop App...
%PYTHON_EXE% backend/desktop_app.py
if %errorlevel% neq 0 (
    echo.
    echo Application stopped or encountered an error.
    pause
)
