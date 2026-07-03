@echo off
REM Stops any GA4 Inventory server currently running on port 8788.
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8788 " ^| findstr "LISTENING"') do (
  echo Killing PID %%a
  taskkill /F /PID %%a
)
pause
