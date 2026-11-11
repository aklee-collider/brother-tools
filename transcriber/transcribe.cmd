@echo off
rem Drag an audio file onto this file, or run: transcribe.cmd meeting.m4a
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python transcribe.py --open %*
) else (
    py transcribe.py --open %*
)
pause
