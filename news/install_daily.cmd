@echo off
rem Registers a daily 07:30 briefing that runs in the background:
rem   - output is logged to news\output\news_log.txt (survives reboots)
rem   - a run missed while the PC was off fires as soon as the PC wakes
rem         (StartWhenAvailable)
rem To remove:  schtasks /delete /tn "BrotherNewsDaily" /f
setlocal
cd /d "%~dp0"
set "PYEXE="
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('where py 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE (
    echo Python not found on PATH. Install Python first ^(setup.cmd checks this^), then re-run.
    pause
    exit /b 1
)

schtasks /create /tn "BrotherNewsDaily" /tr "\"%PYEXE%\" \"%~dp0run_daily.py\"" /sc daily /st 07:30 /f
if errorlevel 1 (
    echo [FIX] Could not create the scheduled task ^(see the error above^).
    pause
    exit /b 1
)

rem StartWhenAvailable: schtasks cannot set it directly — patch via PowerShell.
powershell -NoProfile -Command "Set-ScheduledTask -TaskName 'BrotherNewsDaily' -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries) | Out-Null"
if errorlevel 1 (
    echo [NOTE] The task is registered, but the "run if missed" setting could not be
    echo     applied ^(PowerShell error above^). The 07:30 run still works; a PC that is
    echo     off at 07:30 just skips that day until the next run.
) else (
    echo [OK] Task registered: daily 07:30, missed runs fire after wake,
    echo      output logged to news\output\news_log.txt
)

echo.
echo To test it now:  schtasks /run /tn "BrotherNewsDaily"
echo Then check news\output\news_log.txt
pause
