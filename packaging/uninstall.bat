@echo off
rem Uninstall NIFTYBEES Algo: stops the app, offers a backup of your data, removes the shortcuts and
rem offers to delete this folder. It asks before each step that removes something.
title NIFTYBEES Algo - uninstall
set "APPDIR=%~dp0"
rem Leave the folder first, so it can be deleted at the end.
cd /d "%TEMP%"
rem -ExecutionPolicy Bypass applies to this one PowerShell process only; no Windows setting is changed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%APPDIR%uninstall.ps1"
exit /b %errorlevel%
