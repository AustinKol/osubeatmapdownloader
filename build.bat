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

rem --onedir: no unpacking into %TEMP% on every launch; Python's files go in the "runtime" subfolder
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onedir --console ^
  --name "%APPNAME%" ^
  --contents-directory runtime ^
  --icon assets\icon.ico ^
  --add-data "web;web" ^
  --collect-submodules selenium.webdriver.chrome ^
  --collect-submodules selenium.webdriver.chromium ^
  --exclude-module PIL ^
  app.py || exit /b 1

copy /y tools\release_readme.txt "dist\%APPNAME%\README.txt" >nul || exit /b 1

if exist "dist\%ZIPNAME%" del "dist\%ZIPNAME%"
".venv\Scripts\python.exe" -c "import shutil; shutil.make_archive(r'dist\%ZIPNAME:.zip=%', 'zip', 'dist', r'%APPNAME%')" || exit /b 1

echo.
echo Built dist\%APPNAME%\ and dist\%ZIPNAME%
