@echo off
chcp 65001 >nul
title BELL - Start with Windows + hourly backup + watchdog
cd /d "%~dp0"
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
call scripts\find_python.bat
if not exist .env (echo [X] Run 1_setup.bat first & pause & exit /b 1)
"%PY%" -c "import sys; print(sys.executable)" > "%TEMP%\bell_py.txt"
set /p PYFULL=<"%TEMP%\bell_py.txt"
set "SITE=%~dp0"
set "SITE=%SITE:~0,-1%"
schtasks /end /tn "BELL Website" >nul 2>nul
schtasks /create /f /tn "BELL Website" /sc onstart /ru SYSTEM /rl highest /tr "\"%PYFULL%\" \"%SITE%\serve.py\"" || (echo [X] Could not create the startup task & pause & exit /b 1)
schtasks /create /f /tn "BELL Hourly Backup" /sc hourly /mo 1 /ru SYSTEM /rl highest /tr "\"%PYFULL%\" \"%SITE%\ops\backup.py\"" >nul || echo [!] Could not create the backup task
schtasks /create /f /tn "BELL Failover Watchdog" /sc minute /mo 5 /ru SYSTEM /rl highest /tr "\"%PYFULL%\" \"%SITE%\ops\cf.py\" auto" >nul || echo [!] Could not create the watchdog task
powercfg /change standby-timeout-ac 0 >nul 2>nul
rem Office bell player reaches this server over the school network when the internet is down
netsh advfirewall firewall delete rule name="BELL 8005 (school network)" >nul 2>nul
netsh advfirewall firewall add rule name="BELL 8005 (school network)" dir=in action=allow protocol=TCP localport=8005 remoteip=localsubnet profile=any >nul 2>nul || echo [!] Could not add the firewall rule for port 8005
schtasks /run /tn "BELL Website" >nul
echo.
echo   Done. BELL starts with Windows (port 8005), backs up every hour,
echo   and the watchdog checks every 5 minutes. Log: logs\server.log
timeout /t 5 >nul
start "" http://localhost:8005/admin
pause
