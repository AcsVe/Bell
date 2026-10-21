@echo off
chcp 65001 >nul
title BELL - Turn the bell system ON
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
schtasks /change /tn "BELL Website" /enable >nul 2>nul || (echo [X] The startup task is missing - run 3_autostart.bat as administrator & pause & exit /b 1)
schtasks /run /tn "BELL Website" >nul
echo.
echo   5.iajaward.org (school bell) is ON again (starts with Windows as before).
echo.
pause
