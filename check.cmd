@echo off
rem "Check installation" — verifies Python, libraries, ffmpeg, API key
rem files, and configs for both tools. No network, no cost.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python check_installation.py
) else (
    py check_installation.py
)
echo.
pause
