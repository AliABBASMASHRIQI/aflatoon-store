@echo off
REM Start the Aflatoon Studio app on this computer.
REM Double-click this file, then open the address it prints.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No virtual environment found. Run:
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)

echo.
echo   Aflatoon Studio is starting...
echo   Open this in your browser:  http://127.0.0.1:5000
echo   Sign in with  admin / aflatoon2026   (change it in Settings)
echo.
echo   Close this window to stop the app.
echo.

".venv\Scripts\python.exe" app.py

echo.
echo App stopped.
pause
