@echo off
chcp 65001 >nul
cd /d "%~dp0"
title BELL - Restore latest backup
call scripts\find_python.bat
set PYTHONUTF8=1
echo.
echo   [1] Latest backup made by the PHONE
echo   [2] Latest backup of any device
echo   WARNING: all current bell data on THIS PC will be replaced (a safety copy is kept in data\).
echo.
set /p CH=Choose 1 or 2: 
set TAG=
if "%CH%"=="1" set TAG=--tag phone
if not "%CH%"=="1" if not "%CH%"=="2" (echo   Cancelled. & pause & exit /b 0)
set /p OK=Type YES to continue: 
if /i not "%OK%"=="YES" (echo   Cancelled. & pause & exit /b 0)
schtasks /end /tn "BELL Website" >nul 2>nul
"%SystemRoot%\System32\timeout.exe" /t 2 >nul
"%PY%" ops\restore.py --latest auto %TAG% --yes
if errorlevel 1 (schtasks /run /tn "BELL Website" >nul 2>nul & echo. & echo   [X] Restore failed & pause & exit /b 1)
schtasks /run /tn "BELL Website" >nul 2>nul
echo   Done.
pause
