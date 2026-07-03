@echo off
REM Silent launcher — runs the server without showing any window.
REM Used by the Windows scheduled task at login.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "" /B ".venv\Scripts\pythonw.exe" server.py
