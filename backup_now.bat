@echo off
chcp 65001 >nul
cd /d "%~dp0"
call scripts\find_python.bat
set PYTHONUTF8=1
"%PY%" ops\backup.py
pause
