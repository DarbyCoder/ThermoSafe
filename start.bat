@echo off
REM ThermoSafe: double-click to start. Settings come from .env (copy .env.example to .env first).
cd /d "%~dp0"
if not exist ".env" echo No .env file yet: copy .env.example to .env and fill it in. Starting with practice calls.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY set "PY=python"
start "" cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8000"
%PY% thermo_server.py
pause
