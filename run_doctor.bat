@echo off
REM Setup doctor - checks your installation step by step (Japanese output).
title Analytics Inventory Setup Doctor
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" doctor.py
) else (
  python doctor.py
)
pause
