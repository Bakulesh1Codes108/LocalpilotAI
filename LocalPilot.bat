@echo off
title LocalPilot
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo LocalPilot is not installed yet. Double-click install.bat first.
  pause
  exit /b 1
)
where ollama >nul 2>nul
if %errorlevel%==0 (
  tasklist /fi "imagename eq ollama.exe" | find /i "ollama.exe" >nul || start "" /min ollama serve
)
echo Starting LocalPilot... (close this window to stop it)
.venv\Scripts\python.exe run.py
pause
