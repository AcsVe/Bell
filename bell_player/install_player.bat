@echo off
chcp 65001 >nul
rem Office bell player — run on the office PC connected to the speakers (no admin needed).
cd /d "%~dp0"
if exist "%~dp0BellPlayer.exe" (
  "%~dp0BellPlayer.exe" --setup
  goto :done
)
where python >nul 2>nul && ( python "%~dp0bell_player.py" --setup & goto :done )
where py >nul 2>nul && ( py -3 "%~dp0bell_player.py" --setup & goto :done )
echo   BellPlayer.exe not found next to this file and Python is not installed.
echo   Download: https://github.com/AcsVe/Bell/releases/download/apps/BellPlayer.exe
:done
pause
