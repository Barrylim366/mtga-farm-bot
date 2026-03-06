@echo off
setlocal
cd /d "%~dp0"

python tools\test_builtin_logout.py --full-switch
echo.
pause
