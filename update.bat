@echo off
chcp 65001 >nul
title BELL - Update from GitHub
cd /d "%~dp0"
REM Pulls the latest code from GitHub - data (.env, data, backups) is never touched
where git >nul 2>nul || (echo [X] Git is not installed. & pause & exit /b 1)
call scripts\find_python.bat
set PYTHONUTF8=1
echo If a GitHub sign-in window opens, sign in with the AcsVe account - this is needed only once.
if not exist .git (
  git init -q
  git remote add origin https://github.com/AcsVe/Bell.git
  git fetch -q origin main || (echo [X] Could not reach GitHub. & echo     If GitHub asked you to sign in, sign in with the AcsVe account and run update.bat again. & pause & exit /b 1)
  git reset -q --hard origin/main
  git branch -q -M main
  git branch -q --set-upstream-to=origin/main main
) else (
  echo Downloading the latest version...
  git fetch -q origin main || (echo [X] Could not reach GitHub. & echo     If GitHub asked you to sign in, sign in with the AcsVe account and run update.bat again. & pause & exit /b 1)
  git reset -q --hard origin/main
)
echo Now at:
git log -1 --oneline
"%PY%" -m pip install -q --disable-pip-version-check -r requirements.txt
schtasks /query /tn "BELL Website" >nul 2>nul
if errorlevel 1 goto manual
net session >nul 2>nul || (echo. & echo [!] To restart automatically, run this file as administrator. Or restart the PC. & pause & exit /b 0)
REM مهمة التشغيل معطّلة (لا يبدأ مع ويندوز) والنظام يعمل: يُعاد تشغيله بالنسخة الجديدة وتبقى المهمة معطّلة.
REM معطّلة والنظام متوقف (استُعمل ملف الإيقاف): لا يُشغَّل.
set "TN=BELL Website"
set "WASOFF="
set "WASRUN="
schtasks /query /tn "%TN%" /fo list /v | findstr /i /c:"Scheduled Task State:" | findstr /i "Disabled" >nul && set "WASOFF=1"
schtasks /query /tn "%TN%" /fo list | findstr /i /c:"Status:" | findstr /i "Running" >nul && set "WASRUN=1"
if defined WASOFF if not defined WASRUN goto off
schtasks /end /tn "%TN%" >nul 2>nul
REM انتظار ثانيتين (ping يعمل حتى عند تحويل الإدخال، بخلاف timeout)
ping -n 3 127.0.0.1 >nul
if defined WASOFF schtasks /change /tn "%TN%" /enable >nul
schtasks /run /tn "%TN%" >nul 2>nul
set "RC=%errorlevel%"
if defined WASOFF schtasks /change /tn "%TN%" /disable >nul
if not "%RC%"=="0" (echo. & echo [X] The new version could not be started. If it was switched off, run bell_on.bat as administrator - or 2_start.bat to see the error. & pause & exit /b 1)
echo.
echo Done. BELL was restarted with the new version.
if defined WASOFF echo It still does not start with Windows ^(its startup task stays disabled^).
pause
exit /b 0
:off
echo.
echo Code updated. BELL is switched OFF ^(its startup task is disabled^), so it was not started.
echo To turn it on: run bell_on.bat as administrator - or 2_start.bat for this session only.
pause
exit /b 0
)
echo.
echo Done. BELL was restarted with the new version.
pause
exit /b 0
:manual
echo.
echo Done. Close 2_start.bat and open it again to use the new version.
pause
