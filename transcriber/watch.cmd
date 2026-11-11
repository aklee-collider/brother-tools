@echo off
rem Leave this window open: it watches inbox\ for new recordings and
rem processes them automatically. Ctrl+C stops.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python watch.py
) else (
    py watch.py
)
pause
