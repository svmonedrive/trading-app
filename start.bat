@echo off
rem Double-click to open NIFTYBEES Algo. Keep this window open while you use the app.
rem Works in the release folder (portable Python from setup.bat) and in the source repo (backend\.venv).
title NIFTYBEES Launcher (app on http://localhost:4200)
cd /d "%~dp0"

set "PY=runtime\python\python.exe"
if not exist "%PY%" set "PY=backend\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo  Python is not set up yet. Double-click setup.bat first, then run start.bat again.
  echo.
  pause
  exit /b 1
)

"%PY%" launcher.py --open
pause
