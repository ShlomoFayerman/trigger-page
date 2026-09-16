@echo off
REM Launcher for the trigger page. Double-click this file.
REM Kept in ASCII on purpose: cmd.exe mangles Hebrew in .bat files.
chcp 65001 >nul
cd /d "%~dp0"
echo Starting trigger page server...
python server.py
echo.
echo Server stopped. Press any key to close.
pause >nul
