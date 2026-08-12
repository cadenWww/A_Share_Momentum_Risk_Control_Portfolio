@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo A-Share Momentum Risk-Control Research - One-Time Setup
echo ============================================================
echo.

where py >nul 2>&1
if errorlevel 1 goto missing_python

if exist "%~dp0.venv\Scripts\python.exe" goto install_packages

echo [1/3] Creating a local Python environment...
py -3.14 -m venv "%~dp0.venv" >nul 2>&1
if not errorlevel 1 goto install_packages

echo Python 3.14 was not found. Trying another installed Python 3 version...
py -3 -m venv "%~dp0.venv"
if errorlevel 1 goto failed

:install_packages
echo [2/3] Updating pip...
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed

echo [3/3] Installing dependencies...
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-lock.txt"
if errorlevel 1 goto failed

echo.
echo Installation completed. Run launch_gui.cmd or follow README.md.
pause
exit /b 0

:missing_python
echo ERROR: Python was not found. Install 64-bit Python and try again.
pause
exit /b 1

:failed
echo ERROR: Installation did not complete. Review the message above.
pause
exit /b 1

