@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
start "" ".venv\Scripts\pythonw.exe" -m pet_eye %*
