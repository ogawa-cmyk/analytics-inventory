@echo off
REM GA4 Inventory server — double-click to start.
REM Closing this window stops the server.
title GA4 Inventory Server (port 8788)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo Starting GA4 Inventory server at http://127.0.0.1:8788/
echo Close this window to stop the server.
echo.
".venv\Scripts\python.exe" server.py
pause
