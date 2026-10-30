@echo off
chcp 65001 >nul
cd /d "%~dp0"
title BELL - Back to this PC
call scripts\find_python.bat
set PYTHONUTF8=1
echo.
echo   Move 5.iajaward.org back to THIS PC
echo   1) take the phone's bell data from Google Drive
echo   2) point 5.iajaward.org back to this PC
echo   (On the phone first: press "BELL 1 - Stop emergency" - it uploads the data)
echo.
set /p OK=Type YES to continue: 
if /i not "%OK%"=="YES" (echo   Cancelled. & pause & exit /b 0)
schtasks /end /tn "BELL Website" >nul 2>nul
"%SystemRoot%\System32\timeout.exe" /t 2 >nul
"%PY%" ops\restore.py --latest auto --tag phone --since-marker --yes
if errorlevel 1 (schtasks /run /tn "BELL Website" >nul 2>nul & echo. & echo   [X] Not finished - see the message above & pause & exit /b 1)
schtasks /run /tn "BELL Website" >nul 2>nul
"%SystemRoot%\System32\timeout.exe" /t 8 /nobreak >nul
"%PY%" ops\cf.py server
"%PY%" -c "import sys; sys.path.insert(0,'ops'); from common import *; load_env(); import os; p=os.path.join(backup_root(),'db','ACTIVE_SITE.txt'); os.makedirs(os.path.dirname(p),exist_ok=True); open(p,'w').write('server '+stamp())"
echo.
pause
