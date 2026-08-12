@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\pythonw.exe" goto missing_python

start "" /b "%~dp0.venv\Scripts\pythonw.exe" "%~dp0src\gui_app.pyw"
exit /b 0

:missing_python
echo ERROR: The local environment was not found. Run install_once.cmd first.
pause
exit /b 1

