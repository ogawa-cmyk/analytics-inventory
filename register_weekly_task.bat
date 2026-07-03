@echo off
REM Registers (or updates) the Windows Task Scheduler entry that runs
REM run_weekly_mail.bat daily at the configured send hour (+5 min).
REM Re-run this after changing the send hour in /settings/notifications.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" send_weekly_mail.py --register
pause
