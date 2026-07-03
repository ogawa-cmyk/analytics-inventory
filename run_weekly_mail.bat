@echo off
REM Scheduled task entry - sends the weekly health summary mail.
REM The script itself checks: enabled / correct weekday / not sent this week.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" send_weekly_mail.py > weekly_mail.log 2>&1
