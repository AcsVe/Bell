@echo off
setlocal EnableExtensions
chcp 65001 >nul
title BELL - Install on a new server PC
REM ===================================================================
REM  One file: installs Python + Git if missing, downloads the bell
REM  system to C:\Bell, runs the setup, restores the latest backup from
REM  Google Drive, and starts it with Windows. Run as administrator.
REM ===================================================================
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
set "SITE=C:\Bell"
set "TMPD=%TEMP%\bell_install"
if not exist "%TMPD%" mkdir "%TMPD%"
echo.
echo   School bell - install on this PC (C:\Bell, port 8005, 5.iajaward.org)
echo   -------------------------------------------------------------------
echo   Before you start: Google Drive for desktop is signed in (G:\My Drive\RAS-Bell),
echo   and the Cloudflare tunnel "iajaward" runs on THIS PC (same as the award site).
echo.
if not exist "G:\My Drive\RAS-Bell\db" echo   [!] G:\My Drive\RAS-Bell\db not found - backups cannot be restored until Google Drive is ready.
echo.

REM ---------- 1) Python 3.12 ----------
call :have_python
if not errorlevel 1 goto python_ok
echo [1/6] Installing Python 3.12 ...
where winget >nul 2>nul && winget install -e --id Python.Python.3.12 --scope machine --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
call :add_paths
call :have_python
if not errorlevel 1 goto python_ok
echo       winget not available - downloading from python.org ...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile '%TMPD%\python.exe'" || (echo [X] Could not download Python - check the internet & pause & exit /b 1)
"%TMPD%\python.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0
call :add_paths
call :have_python
if errorlevel 1 (echo [X] Python did not install. Install Python 3.12 64-bit from python.org with "Add python.exe to PATH", then run this file again. & pause & exit /b 1)
:python_ok
echo [1/6] Python: OK

REM ---------- 2) Git ----------
call :add_paths
where git >nul 2>nul && goto git_ok
echo [2/6] Installing Git ...
where winget >nul 2>nul && winget install -e --id Git.Git --scope machine --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
call :add_paths
where git >nul 2>nul && goto git_ok
echo       winget not available - downloading from GitHub ...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; $r=Invoke-RestMethod 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers @{'User-Agent'='bell'}; $a=$r.assets | Where-Object { $_.name -match '^Git-.*-64-bit\.exe$' } | Select-Object -First 1; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile '%TMPD%\git.exe'" || (echo [X] Could not download Git & pause & exit /b 1)
"%TMPD%\git.exe" /VERYSILENT /NORESTART /NOCANCEL /SP- /SUPPRESSMSGBOXES
call :add_paths
where git >nul 2>nul || (echo [X] Git did not install. Install it from git-scm.com, then run this file again. & pause & exit /b 1)
:git_ok
echo [2/6] Git: OK

REM ---------- 3) Download the bell system ----------
if exist "%SITE%\.git" (
  echo [3/6] C:\Bell already exists - updating it
  pushd "%SITE%" && git fetch -q origin main && git reset -q --hard origin/main & popd
) else (
  if exist "%SITE%" (echo [X] C:\Bell exists but is not a bell installation - rename it and run again & pause & exit /b 1)
  echo [3/6] Downloading the bell system to C:\Bell ...
  git clone -q https://github.com/AcsVe/Bell.git "%SITE%" || (echo [X] Could not download from GitHub & pause & exit /b 1)
)

REM ---------- 4) Setup (.env, packages, tunnel) ----------
echo [4/6] Setup - answer the questions. At "Copy all data from the old Render server" answer N.
echo.
pushd "%SITE%"
call 1_setup.bat
popd

REM ---------- 5) Data ----------
echo.
echo [5/6] Bring the data from the latest backup in Google Drive?
echo       (stages, programs, teachers, codes, tones, central bell - everything)
set "RB=y"
set /p "RB=      Restore now? (Y/n): "
if /i "%RB%"=="n" goto skip_restore
pushd "%SITE%"
call scripts\find_python.bat
"%PY%" ops\restore.py --latest auto --yes || echo [!] Restore did not finish - you can run restore_backup.bat later
popd
:skip_restore

REM ---------- 6) Start with Windows + check ----------
echo.
echo [6/6] Starting it with Windows (hourly backup, watchdog, school-network port)...
pushd "%SITE%"
call 3_autostart.bat
call bell_check.bat
popd
echo.
echo   Installed. Admin page: https://5.iajaward.org/admin
echo   Office speakers PC: install BellPlayer.exe (admin page - Central bell - Office player).
pause
exit /b 0

:have_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
exit /b %errorlevel%

:add_paths
for %%D in ("C:\Program Files\Python312" "C:\Program Files\Python312\Scripts" "%LOCALAPPDATA%\Programs\Python\Python312" "%LOCALAPPDATA%\Programs\Python\Python312\Scripts" "C:\Program Files\Git\cmd") do (
  if exist "%%~D" call set "PATH=%%~D;%%PATH%%"
)
exit /b 0
