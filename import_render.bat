@echo off
chcp 65001 >nul
cd /d "%~dp0"
title BELL - Copy all data from the old Render server
call scripts\find_python.bat
set PYTHONUTF8=1
schtasks /end /tn "BELL Website" >nul 2>nul
"%PY%" ops\import_render.py
schtasks /run /tn "BELL Website" >nul 2>nul
pause
