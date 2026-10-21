@echo off
cd /d "%~dp0"
net session >nul 2>nul || (echo [X] Run as administrator. & pause & exit /b 1)
schtasks /end /tn "BELL Website" >nul 2>nul
schtasks /delete /f /tn "BELL Website" >nul 2>nul
schtasks /delete /f /tn "BELL Hourly Backup" >nul 2>nul
schtasks /delete /f /tn "BELL Failover Watchdog" >nul 2>nul
echo Stopped and removed the BELL tasks.
pause
