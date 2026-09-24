@echo off
cd /d "%~dp0"
rem Open browser after 2s delay
start "" /min cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765"
".venv\Scripts\python.exe" -m web.server
echo.
echo Server exited. Check errors above if any.
pause
