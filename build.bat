@echo off
rem Builds the portable release:
rem   dist\osu! Beatmap Downloader\          (the app folder)
rem   dist\osu-beatmap-downloader-win64.zip  (upload this to GitHub Releases)
setlocal
cd /d "%~dp0"
set "APPNAME=osu! Beatmap Downloader"
set "ZIPNAME=osu-beatmap-downloader-win64.zip"

if not exist ".venv\Scripts\python.exe" python -m venv .venv || exit /b 1
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt -r requirements-dev.txt || exit /b 1
".venv\Scripts\python.exe" tools\make_icon.py || exit /b 1

rem PyInstaller replaces the app folder, so keep data\ and downloads\ from test runs aside
set "KEEP=dist\_keep"
if exist "%KEEP%" rmdir /s /q "%KEEP%"
mkdir "%KEEP%"
if exist "dist\%APPNAME%\data" move "dist\%APPNAME%\data" "%KEEP%\data" >nul
if exist "dist\%APPNAME%\downloads" move "dist\%APPNAME%\downloads" "%KEEP%\downloads" >nul

rem --onedir: no unpacking into %TEMP% on every launch; Python's files go in the "runtime" subfolder
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onedir --console ^
  --name "%APPNAME%" ^
  --contents-directory runtime ^
  --icon assets\icon.ico ^
  --add-data "web;web" ^
  --collect-submodules selenium.webdriver.chrome ^
  --collect-submodules selenium.webdriver.chromium ^
  --exclude-module PIL ^
  app.py || goto :restore

copy /y tools\release_readme.txt "dist\%APPNAME%\README.txt" >nul || goto :restore

if exist "dist\%ZIPNAME%" del "dist\%ZIPNAME%"
".venv\Scripts\python.exe" tools\make_zip.py "dist\%APPNAME%" "dist\%ZIPNAME%" || goto :restore
set "OK=1"

:restore
if exist "%KEEP%\data" move "%KEEP%\data" "dist\%APPNAME%\data" >nul
if exist "%KEEP%\downloads" move "%KEEP%\downloads" "dist\%APPNAME%\downloads" >nul
rmdir "%KEEP%" 2>nul
if not defined OK exit /b 1

echo.
echo Built dist\%APPNAME%\ and dist\%ZIPNAME%
