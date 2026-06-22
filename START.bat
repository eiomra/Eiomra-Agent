@echo off
echo ============================================
echo   AI Browser Agent - Setup & Run
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org
    pause & exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Install from https://nodejs.org
    pause & exit /b 1
)

echo [1/4] Installing Python dependencies...
cd /d "%~dp0backend"
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install Python deps
    pause & exit /b 1
)

echo.
echo [2/4] Installing Playwright browsers...
playwright install chromium
if errorlevel 1 (
    echo [ERROR] Failed to install Playwright chromium
    pause & exit /b 1
)

echo.
echo [3/4] Installing frontend dependencies...
cd /d "%~dp0frontend"
npm install
if errorlevel 1 (
    echo [ERROR] Failed npm install
    pause & exit /b 1
)

echo.
echo [4/4] Starting services...
echo.

start "AI Browser - Backend" cmd /k "cd /d %~dp0backend && python agent.py"
timeout /t 3 /nobreak >nul
start "AI Browser - Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
timeout /t 4 /nobreak >nul
start http://localhost:3000

echo Opened http://localhost:3000
echo.
pause
