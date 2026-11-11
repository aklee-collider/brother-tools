@echo off
rem One-time setup: installs the two Python libraries this tool needs.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python -m pip install -r requirements.txt
) else (
    py -m pip install -r requirements.txt
)
pause
