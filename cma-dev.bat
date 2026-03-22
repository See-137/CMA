@echo off
title CMA - Cost Monitoring Agent (Dev)
echo.
echo   ===================================
echo      $  Cost Monitoring Agent
echo      Dev Mode - No Docker
echo   ===================================
echo.

echo   [starting] Backend on http://localhost:8000
start /b "CMA-Backend" cmd /c "cd /d %~dp0backend && python -m uvicorn app.main:app --reload --port 8000"

echo   [waiting]  Backend health check...
:wait_backend
timeout /t 1 /nobreak >nul
curl -s http://localhost:8000/api/v1/setup/status >nul 2>nul
if %ERRORLEVEL% neq 0 goto wait_backend
echo   [ok]       Backend ready.

echo   [starting] Frontend on http://localhost:5173
start /b "CMA-Frontend" cmd /c "cd /d %~dp0frontend && npm run dev"

echo.
echo   Open http://localhost:5173 in your browser
echo   Press any key to stop both services...
echo.
pause >nul

echo.
echo   Shutting down...
taskkill /f /fi "WINDOWTITLE eq CMA-Backend" >nul 2>nul
taskkill /f /fi "WINDOWTITLE eq CMA-Frontend" >nul 2>nul
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>nul
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5173 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>nul
echo   Stopped.
