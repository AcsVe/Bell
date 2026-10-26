@echo off
chcp 65001 >nul
title BELL - First time setup
cd /d "%~dp0"
set PYTHONHOME=
set PYTHONPATH=
set DATABASE_URL=
set PYTHONUTF8=1
echo =====================================================
echo   School Bell (5.iajaward.org) - setup on this PC
echo =====================================================
where python >nul 2>nul || (echo [X] Python is not installed. Install Python 3.12 64-bit from python.org with "Add python.exe to PATH". & pause & exit /b 1)
if not exist venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv venv || (echo [X] Could not create venv & pause & exit /b 1)
)
call scripts\find_python.bat
echo Installing packages...
"%PY%" -m pip install -q --disable-pip-version-check --timeout 60 -r requirements.txt || (echo [X] pip install failed - check the internet & pause & exit /b 1)

if not exist .env (
  "%PY%" ops\setup_local.py || (pause & exit /b 1)
) else (
  echo .env already exists - keeping it. To change settings run: venv\Scripts\python ops\setup_local.py
  echo Checking the optional Microsoft Graph mail settings...
  "%PY%" ops\setup_local.py --mail
)

echo.
echo Creating database tables...
"%PY%" -c "import sys; sys.path.insert(0,'ops'); from common import load_env; load_env(); import app" || (echo [X] Database setup failed & pause & exit /b 1)

echo.
set "IMPORT=n"
set /p "IMPORT=Copy all data from the old Render server (stages, programs, teachers, codes, tones) now? (y/N): "
if /i "%IMPORT%"=="y" "%PY%" ops\import_render.py

echo.
echo Connecting 5.iajaward.org to this PC (Cloudflare)...
"%PY%" ops\cf.py setup

echo.
echo =====================================================
echo   Setup finished.
echo   Next: 3_autostart.bat (right-click - Run as administrator)
echo         to start it with Windows + hourly backup + watchdog
echo   Test now: 2_start.bat then open http://localhost:8005/admin
echo =====================================================
pause
