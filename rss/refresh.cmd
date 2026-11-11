@echo off
rem Double-click to refresh headlines and open the page.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python fetch_feeds.py --open
) else (
    py fetch_feeds.py --open
)
pause
