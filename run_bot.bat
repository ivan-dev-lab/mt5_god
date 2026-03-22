@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_bot.py %*
    set "EXIT_CODE=%ERRORLEVEL%"
    goto :finish
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python run_bot.py %*
    set "EXIT_CODE=%ERRORLEVEL%"
    goto :finish
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 run_bot.py %*
    set "EXIT_CODE=%ERRORLEVEL%"
    goto :finish
)

echo Python not found. Install Python or create .venv in this project.
set "EXIT_CODE=9009"

:finish
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Bot launcher exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
