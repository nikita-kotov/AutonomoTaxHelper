@echo off
rem Starts Autonomo Tax Helper with an icon in the system tray (no console window stays open).
rem Open the interface by double-clicking the icon; stop the app from the icon's menu or the page's "Exit" button.
rem Data location: see README, section "Data and privacy". Start-up problems are written to app.log there.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
".venv\Scripts\python.exe" -c "import flask, pystray, PIL" 2>nul || (
  echo Installing requirements...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" app.py --tray
) else (
  ".venv\Scripts\python.exe" app.py
  if errorlevel 1 pause
)
