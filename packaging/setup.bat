@echo off
rem One-time setup for NIFTYBEES Algo (needs internet). Safe to run again.
rem Downloads a portable Python into this folder - nothing is installed system-wide, no admin rights needed.
rem Then creates the "NIFTYBEES Algo" shortcuts and opens the app.
title NIFTYBEES Algo - setup
cd /d "%~dp0"
rem -ExecutionPolicy Bypass applies to this one PowerShell process only; no Windows setting is changed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo  Setup did not finish - see the message above.
  pause
  exit /b 1
)
timeout /t 5 >nul
