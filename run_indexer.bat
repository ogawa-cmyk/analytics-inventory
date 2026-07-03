@echo off
REM Scheduled task entry — runs indexer.py then auto_diagnose.py.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" indexer.py > indexer.log 2>&1
".venv\Scripts\python.exe" auto_diagnose.py >> indexer.log 2>&1
