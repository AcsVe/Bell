@echo off
setlocal EnableExtensions
chcp 65001 >nul
title IAJ - Run the Cloudflare tunnel "iajaward" on THIS PC
cd /d "%~dp0"
REM The main tunnel serves EVERY iajaward.org site (award, ATT 3., bell 5. ...) and sends each address to its port
REM ON THE PC WHERE THE TUNNEL RUNS. Run this only on the PC that is (or replaces) the school server.
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
call scripts\find_python.bat
set PYTHONUTF8=1
if not exist .env (echo [X] Run 1_setup.bat first ^(it saves the Cloudflare token^) & pause & exit /b 1)
echo.
echo   Checking where the tunnel "iajaward" runs now...
"%PY%" ops\cf.py connectors > "%TEMP%\iaj_conn.txt" 2>"%TEMP%\iaj_conn_detail.txt"
set /p NCONN=<"%TEMP%\iaj_conn.txt"
type "%TEMP%\iaj_conn_detail.txt"
if not "%NCONN%"=="0" (
  echo.
  echo   [!] The tunnel is already running on %NCONN% other connector^(s^) - probably the OLD server PC.
  echo       If both run, visitors are split between the two PCs and sites missing on one of them FAIL.
  echo       Turn the tunnel off on the old PC first ^(Services - Cloudflared - Stop + Disabled^), or switch the old PC off.
  set "GO=n"
  set /p "GO=      Run it on THIS PC anyway? (y/N): "
)
if not "%NCONN%"=="0" if /i not "%GO%"=="y" (echo   Cancelled. & pause & exit /b 0)

REM ---------- cloudflared ----------
set "CFD="
for %%P in ("C:\Program Files\cloudflared\cloudflared.exe" "C:\Program Files (x86)\cloudflared\cloudflared.exe") do if exist "%%~P" set "CFD=%%~P"
if not defined CFD for /f "delims=" %%P in ('where cloudflared 2^>nul') do if not defined CFD set "CFD=%%P"
if not defined CFD (
  echo   Installing cloudflared...
  where winget >nul 2>nul && winget install -e --id Cloudflare.cloudflared --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
  for %%P in ("C:\Program Files\cloudflared\cloudflared.exe" "C:\Program Files (x86)\cloudflared\cloudflared.exe") do if exist "%%~P" set "CFD=%%~P"
)
if not defined CFD (
  if not exist "C:\Program Files\cloudflared" mkdir "C:\Program Files\cloudflared"
  powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'C:\Program Files\cloudflared\cloudflared.exe'" || (echo [X] Could not download cloudflared & pause & exit /b 1)
  set "CFD=C:\Program Files\cloudflared\cloudflared.exe"
)
echo   cloudflared: %CFD%

REM ---------- install it as a Windows service with the tunnel token ----------
"%PY%" ops\cf.py main-token > "%TEMP%\iaj_tok.txt" || (del "%TEMP%\iaj_tok.txt" >nul 2>nul & echo [X] Could not get the tunnel token from Cloudflare & pause & exit /b 1)
set /p TOK=<"%TEMP%\iaj_tok.txt"
del "%TEMP%\iaj_tok.txt" >nul 2>nul
sc query cloudflared >nul 2>nul && ("%CFD%" service uninstall >nul 2>nul & timeout /t 3 >nul)
"%CFD%" service install %TOK% || (set "TOK=" & echo [X] Could not install the cloudflared service & pause & exit /b 1)
set "TOK="
sc config cloudflared start= auto >nul 2>nul
sc start cloudflared >nul 2>nul
echo.
echo   Waiting for the tunnel to connect...
timeout /t 15 /nobreak >nul
"%PY%" ops\cf.py connectors 2>nul > "%TEMP%\iaj_conn.txt"
set /p NCONN=<"%TEMP%\iaj_conn.txt"
echo   Connectors online now: %NCONN%
echo.
echo   [OK] The tunnel "iajaward" runs on this PC and starts with Windows.
echo        Every iajaward.org address now reaches THIS PC - install the other systems here too (award site, ATT).
echo        Check: bell_check.bat
pause
