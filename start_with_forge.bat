@echo off
setlocal

rem Starts mtga-farm-bot with the Forge advisor choosing its plays.
rem The bot's own launcher (start_windows.bat) has to have run once first:
rem it creates the bot's Python environment and walks through its first-time setup.

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
set "BOT_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"

if not exist "%BOT_PYTHON%" (
    echo [ERROR] The bot's Python environment was not found.
    echo Run start_windows.bat once, close the bot, then start this file again.
    pause
    exit /b 1
)
if not exist "%ROOT_DIR%\forge_advisor\build\classes" (
    echo [ERROR] The Forge advisor is not built. Run:  python scripts\build_advisor.py --forge
    pause
    exit /b 1
)

"%BOT_PYTHON%" "%ROOT_DIR%\scripts\start_bot.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [WARN] The bot ended with code %RC%.
    pause
)
exit /b %RC%
