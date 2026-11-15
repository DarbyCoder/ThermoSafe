@echo off
REM ThermoSafe: double-click to start. Settings come from .env (copy .env.example to .env first).
cd /d "%~dp0"
if not exist ".env" echo No .env file yet: copy .env.example to .env and fill in the ThermoSafe keys.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY set "PY=python"
start "" cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8001"
%PY% scripts\start_local.py
pause
