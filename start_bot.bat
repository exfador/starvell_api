@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [*] Starting Starvell bot...

REM Create venv if not exists
if not exist ".venv\" (
    echo [*] Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
)

REM Activate venv
call ".venv\Scripts\activate.bat"

REM Upgrade pip and install dependencies
python -m pip install --upgrade pip >nul
if exist "requirements.txt" (
    echo [*] Installing requirements...
    pip install -r requirements.txt
)

REM Run the bot
python run_bot.py

pause


