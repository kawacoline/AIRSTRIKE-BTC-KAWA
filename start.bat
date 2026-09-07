@echo off
echo Starting Airstrike BTC Trading Bot...

:: Pull latest updates from Git
echo Pulling latest updates from Git...
git pull

:: Activate the virtual environment
if not exist .venv (
    echo Virtual environment not found. Please run setup.bat first.
    pause
    exit /b
)

call .venv\Scripts\activate.bat

:: Install/update dependencies (catches new packages from git pull)
echo [Bot] Checking and installing dependencies from requirements.txt...
pip install -r requirements.txt



:: Run the main application
python main.py

pause
