@echo off
title Voiceover Studio Pro
echo ===================================================
echo   Starting Voiceover Studio Pro (Windows)
echo ===================================================

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.9 or newer from https://www.python.org/
    pause
    exit /b 1
)

echo Checking dependencies...
python -m pip install -r requirements.txt --quiet

echo Launching Voiceover Studio Pro Desktop App...
python backend/desktop_app.py
if %errorlevel% neq 0 (
    pause
)
