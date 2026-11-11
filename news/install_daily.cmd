@echo off
rem Registers a daily 07:30 rebuild (no browser popup — just open index.html).
rem To remove:  schtasks /delete /tn "BrotherNewsDaily" /f
cd /d "%~dp0"
set "PYEXE="
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('where py 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE (
    echo Python not found on PATH. Install Python first, then re-run.
    pause
    exit /b 1
)
schtasks /create /tn "BrotherNewsDaily" /tr "\"%PYEXE%\" \"%~dp0fetch_news.py\"" /sc daily /st 07:30 /f
pause
