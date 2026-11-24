@echo off
:: ICT Trading Bot — Windows Start Script
:: Uses the project's virtual environment Python

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found at .venv
    echo Please create it first: python -m venv .venv
    echo Then install dependencies: .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo =========================================
echo   ICT Trading Bot — Starting...
echo =========================================
echo.

.venv\Scripts\python.exe main.py %*

pause
