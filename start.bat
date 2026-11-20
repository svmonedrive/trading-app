@echo off
rem Double-click to open the NIFTYBEES dashboard. Use the "Start Backend Server" button on the page.
title NIFTYBEES Launcher (dashboard on http://localhost:4200)
cd /d "%~dp0"
"backend\.venv\Scripts\python.exe" launcher.py --open
pause
