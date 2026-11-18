@echo off
rem Add your own event or task to the work calendar, then answer the prompts.
rem To remove one later: edit calendar.json in Notepad, then run open.cmd.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python add_entry.py
) else (
    py add_entry.py
)
pause
