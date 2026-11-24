@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\pythonw.exe" (
  echo Missing runtime\pythonw.exe. Please keep the complete app folder together.
  pause
  exit /b 1
)
if not exist "app.py" (
  echo Missing app.py. Please keep the complete app folder together.
  pause
  exit /b 1
)
start "" "runtime\pythonw.exe" "app.py" %*
exit /b 0
