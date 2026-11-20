@echo off
rem Opens NIFTYBEES Algo - same as the "NIFTYBEES Algo" shortcut. Run setup.bat once first.
rem Starts the app hidden in the background; stop it with the Quit button in the app.
if not exist "%~dp0runtime\python\pythonw.exe" (
  echo Python is not set up yet - double-click setup.bat first.
  pause
  exit /b 1
)
start "" "%~dp0runtime\python\pythonw.exe" "%~dp0launcher.py" --app
