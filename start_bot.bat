@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [*] Starting Starvell bot...


REM Dependencies should be installed once via setup_bot.bat

REM Run the bot
python run_bot.py

pause


