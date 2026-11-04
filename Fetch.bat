@echo off
rem Double-click to start Fetch (Windows). Needs Python 3.10+ installed.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 run.py %*) || (python run.py %*)
if errorlevel 1 pause
