@echo off
title LocalPilot setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
pause
