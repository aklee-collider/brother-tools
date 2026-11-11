@echo off
rem Double-click to rebuild the briefing and open it.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python fetch_news.py --open
) else (
    py fetch_news.py --open
)
pause
