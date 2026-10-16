@echo off
setlocal
cd /d "%~dp0"
if not exist .api-venv\Scripts\python.exe (
  python -m venv .api-venv
  if errorlevel 1 goto failed
)
.api-venv\Scripts\python -m pip install -r requirements-api.txt
if errorlevel 1 goto failed
if not exist .env .api-venv\Scripts\python tools\setup_api.py
if errorlevel 1 goto failed
.api-venv\Scripts\python -m alembic upgrade head
if errorlevel 1 goto failed
.api-venv\Scripts\python -m tiktok_api.run_local
if errorlevel 1 goto failed
exit /b 0
:failed
echo API startup failed. Read the error above. Legacy app and data are unchanged.
pause
exit /b 1
