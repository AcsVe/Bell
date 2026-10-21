@echo off
REM Sets PY to the Python used by this site (venv if it exists, otherwise the one saved during setup)
set PYTHONHOME=
set PYTHONPATH=
set "PY="
if exist "%~dp0..\venv\Scripts\python.exe" set "PY=%~dp0..\venv\Scripts\python.exe"
if not defined PY if exist "%~dp0..\pyexe.txt" set /p PY=<"%~dp0..\pyexe.txt"
if not defined PY set "PY=python"
exit /b 0
