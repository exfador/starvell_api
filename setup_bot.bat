@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [*] Setting up Starvell bot environment...

REM Upgrade pip and install dependencies
python -m pip install --upgrade pip
if exist "requirements.txt" (
    echo [*] Installing requirements from requirements.txt...
    pip install -r requirements.txt
)

echo.
echo [*] Setup completed. You can now run start_bot.bat to start the bot.

pause


