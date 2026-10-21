@echo off
chcp 65001 >nul
title BELL - check and fix
cd /d "%~dp0"
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
call scripts\find_python.bat
set DATABASE_URL=
set PYTHONUTF8=1
"%PY%" ops\check.py
pause
