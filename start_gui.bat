@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo The app is not installed. Run setup.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" run_gui.py
if errorlevel 1 (
  echo.
  echo The app stopped with an error. See the debug log shown by the app.
  pause
)

