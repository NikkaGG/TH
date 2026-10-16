@echo off
rem TikTok Lead Hunter: first run installs everything, later runs just start the server.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv || goto :nopython
  echo [2/3] Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
  echo [3/3] Installing Chromium for Playwright...
  ".venv\Scripts\python.exe" -m playwright install chromium || goto :fail
)

echo Starting server: http://127.0.0.1:8000  (close this window to stop)
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --port 8000
pause
exit /b 0

:nopython
echo Python 3.11+ not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1

:fail
echo Installation failed, see the error above.
pause
exit /b 1
