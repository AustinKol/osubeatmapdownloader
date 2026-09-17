@echo off
title osu! Beatmap Downloader
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python isn't installed. Get it from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" during setup^), then run this again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting things up, this takes a minute...
    python -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto :fail

".venv\Scripts\python.exe" app.py
exit /b 0

:fail
echo.
echo Setup failed. Check your internet connection and try again.
pause
exit /b 1
