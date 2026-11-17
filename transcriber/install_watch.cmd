@echo off
rem Registers the recording watcher to start automatically at logon, with no
rem window — recordings keep processing without an open console.
rem   - activity is logged to transcriber\output\watch.log
rem   - safe next to the manual watch.cmd: a second copy refuses to start
rem To remove:  schtasks /delete /tn "BrotherTranscribeWatch" /f
setlocal
cd /d "%~dp0"

rem pythonw.exe (windowless python) lives in the same folder as python.exe.
set "PYDIR="
for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYDIR set "PYDIR=%%~dpi"
if not defined PYDIR (
    echo pythonw.exe not found. Install Python with "Add python.exe to PATH"
    echo ^(setup.cmd checks this^), then re-run.
    pause
    exit /b 1
)
set "PYW=%PYDIR%pythonw.exe"
if not exist "%PYW%" (
    echo Expected to find pythonw at "%PYW%" but it is not there.
    echo Reinstall Python with "Add python.exe to PATH", then re-run.
    pause
    exit /b 1
)

schtasks /create /tn "BrotherTranscribeWatch" /tr "\"%PYW%\" \"%~dp0watch.py\"" /sc onlogon /f
if errorlevel 1 (
    echo [FIX] Could not create the scheduled task ^(see the error above^).
    pause
    exit /b 1
)
echo [OK] The watcher will start automatically at logon (no window).
echo      Activity log: transcriber\output\watch.log
echo      To stop it for good:  schtasks /delete /tn "BrotherTranscribeWatch" /f
echo      Tip: watch.cmd still works for a manual session — only one watcher
echo      can run at a time; a second copy exits with a clear message.
pause
