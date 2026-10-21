@echo off
chcp 65001 >nul
title BELL - Turn the bell system OFF
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
REM Stops 5.iajaward.org only - the other systems on this PC keep running.
REM The office bell player and the phone/desktop apps keep ringing from their saved schedule.
schtasks /end /tn "BELL Website" >nul 2>nul
schtasks /change /tn "BELL Website" /disable >nul 2>nul
echo.
echo   5.iajaward.org (school bell) is OFF (also after restarting the PC).
echo   Devices keep ringing from their saved schedule.
echo   To turn it back on: bell_on.bat (as administrator)
echo.
pause
