@echo off
setlocal
cd /d "%~dp0"

rem 3.12 is preferred; 3.14 is unsupported until asammdf's zstd dependency ships Windows wheels for it.
set "PY_VERSION="
for %%v in (3.12 3.13 3.11 3.10) do (
  if not defined PY_VERSION (
    py -%%v --version >nul 2>&1
    if not errorlevel 1 set "PY_VERSION=%%v"
  )
)
if not defined PY_VERSION (
  echo Python 3.10-3.13 is required but none was found. Python 3.12 is recommended.
  echo Install Python 3.12 from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)

echo Creating the CANedge Uploader environment with Python %PY_VERSION%...
py -%PY_VERSION% -m venv .venv
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
