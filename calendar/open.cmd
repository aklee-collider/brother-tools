@echo off
rem Rebuild the work calendar from all meeting results + your manual
rem entries, then open it in the browser. Run this after new meetings.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python build_calendar.py --open
) else (
    py build_calendar.py --open
)
if errorlevel 1 pause
