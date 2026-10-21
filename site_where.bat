@echo off
chcp 65001 >nul
cd /d "%~dp0"
call scripts\find_python.bat
set PYTHONUTF8=1
"%PY%" ops\cf.py status
echo.
schtasks /query /tn "BELL Failover Watchdog" >nul 2>nul || echo [!] The watchdog task is missing - right-click 3_autostart.bat and choose "Run as administrator"
echo.
pause
