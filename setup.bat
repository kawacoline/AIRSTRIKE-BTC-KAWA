@echo off
echo ================================
echo  Airstrike BTC Kawa — Setup
echo ================================

:: Check if python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not added to PATH. 
    echo Please install Python 3.10+ and ensure "Add Python to PATH" is checked.
    pause
    exit /b
)

:: Create virtual environment if it doesn't exist
if not exist .venv (
    echo Creating Python virtual environment...
    python -m venv .venv
)

:: Activate the virtual environment
call .venv\Scripts\activate.bat

:: Upgrade pip silently
python -m pip install --upgrade pip -q

:: Install all dependencies from requirements.txt
echo Installing dependencies...
pip install -r requirements.txt

:: Auto-install MetaTrader 5 if not present
if not exist mt5_portable\terminal64.exe (
    echo.
    echo MetaTrader 5 not found — starting auto-installation...
    python install_mt5.py
) else (
    echo MetaTrader 5 already installed in mt5_portable\
)

echo.
echo ======================================
echo  Setup complete!
echo  Configure your .env file, then run:
echo    start.bat
echo ======================================
pause
