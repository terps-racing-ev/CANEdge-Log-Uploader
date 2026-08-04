@echo off
setlocal
cd /d "%~dp0"

py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.12 is required but was not found.
  echo Install Python 3.12 from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)

echo Creating the CANedge Uploader environment...
py -3.12 -m venv .venv
if errorlevel 1 goto :failed

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :failed

echo.
echo Setup complete. Double-click start_gui.bat to run the uploader.
pause
exit /b 0

:failed
echo.
echo Setup failed. See README.md for supported Python versions and troubleshooting.
pause
exit /b 1
