@echo off
rem One-time guided setup for BOTH tools. Safe to re-run any time.
rem At the end it runs the same check as check.cmd — anything still missing
rem is listed with exact fix instructions.
setlocal
cd /d "%~dp0"

echo === brother-tools setup ===
echo.

rem -- 1. Python --
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py"
)
if not defined PY (
    echo [FIX] Python is not installed.
    echo     1. Go to https://www.python.org/downloads/ and install it.
    echo     2. IMPORTANT: on the first installer screen, tick
    echo        "Add python.exe to PATH".
    echo     3. Then run setup.cmd again.
    pause
    exit /b 1
)
echo [OK] Python found.
echo.

rem -- 2. Libraries for both tools --
echo Installing libraries (transcriber + news) ...
%PY% -m pip install --quiet --disable-pip-version-check -r transcriber\requirements.txt -r news\requirements.txt
if errorlevel 1 (
    echo [FIX] Installing the libraries failed. Check your internet
    echo     connection and run setup.cmd again. If it keeps failing, run:
    echo     %PY% -m pip install -r transcriber\requirements.txt -r news\requirements.txt
    pause
    exit /b 1
)
echo [OK] Libraries installed.
echo.

rem -- 3. First-run key files: create .env from the example if missing --
if not exist "transcriber\.env" (
    copy /y "transcriber\.env.example" "transcriber\.env" >nul
    echo [NEXT STEP] transcriber\.env was created for you — open it and paste
    echo     your two API keys. Get them:
    echo       Zhipu BigModel (transcription): https://open.bigmodel.cn
    echo       DeepSeek (summaries):           https://platform.deepseek.com
)
if not exist "news\.env" (
    copy /y "news\.env.example" "news\.env" >nul
    echo [NEXT STEP] news\.env was created for you — open it and paste your
    echo     DeepSeek key (optional; without it the briefing shows raw headlines).
)
echo.

rem -- 4. Full check --
echo Running the installation check ...
echo.
%PY% check_installation.py
set "CHECKRC=%errorlevel%"
echo.
if not "%CHECKRC%"=="0" (
    echo Setup is NOT finished yet — fix the items above, then run check.cmd.
) else (
    echo Setup complete. Next: watch inbox\ via transcriber\watch.cmd, and
    echo news\refresh.cmd for the daily briefing.
)
pause
exit /b %CHECKRC%
