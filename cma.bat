@echo off
setlocal
title CMA - Cost Monitoring Agent
cd /d "%~dp0"

echo.
echo   ===================================
echo      $  Cost Monitoring Agent
echo      Observe. Budget. Control.
echo   ===================================
echo.

if "%1"=="stop" goto stop_cma
if "%1"=="status" goto status_cma
if "%1"=="logs" goto logs_cma
if "%1"=="reset" goto reset_cma

:: ── Default: Start ─────────────────────────────────────────────

where docker >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo   [error] Docker not found. Install Docker Desktop first.
    echo   https://www.docker.com/products/docker-desktop
    echo.
    pause
    exit /b 1
)

docker info >nul 2>nul
if %ERRORLEVEL% equ 0 goto docker_ready

echo   [info] Docker not running. Starting Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
echo   Waiting for Docker engine...

:wait_docker
timeout /t 3 /nobreak >nul
docker info >nul 2>nul
if %ERRORLEVEL% neq 0 goto wait_docker
echo   [ok] Docker engine ready.
echo.

:docker_ready

:: Check if already running
docker compose ps --status running 2>nul | findstr "backend" >nul 2>nul
if %ERRORLEVEL% neq 0 goto start_cma

echo   [ok] CMA is already running!
start "" http://localhost:5173
echo   Opening browser...
echo.
echo   Commands:
echo     cma stop    - Stop CMA
echo     cma status  - Check status
echo     cma logs    - View logs
echo     cma reset   - Reset database
echo.
pause
exit /b 0

:start_cma
echo   Building and starting CMA...
echo.
docker compose up --build -d
if %ERRORLEVEL% neq 0 (
    echo.
    echo   [error] Failed to start. Check Docker Desktop is running.
    echo.
    pause
    exit /b 1
)

echo.
echo   Waiting for services to be ready...

:wait_ready
timeout /t 2 /nobreak >nul
curl -s http://localhost:5173 >nul 2>nul
if %ERRORLEVEL% neq 0 goto wait_ready

echo.
echo   [ok] CMA is running!
echo.
start "" http://localhost:5173
echo   Browser opened at http://localhost:5173
echo.
echo   CMA runs in the background. You can close this window.
echo.
echo   Commands:
echo     cma stop    - Stop CMA
echo     cma status  - Check status
echo     cma logs    - View logs
echo     cma reset   - Reset database
echo.
pause
exit /b 0

:: ── Stop ───────────────────────────────────────────────────────
:stop_cma
echo   Stopping CMA...
docker compose down
echo   [ok] CMA stopped.
pause
exit /b 0

:: ── Status ─────────────────────────────────────────────────────
:status_cma
docker compose ps
pause
exit /b 0

:: ── Logs ───────────────────────────────────────────────────────
:logs_cma
docker compose logs -f --tail 50
exit /b 0

:: ── Reset ──────────────────────────────────────────────────────
:reset_cma
echo   This will delete all data and reset the setup wizard.
set /p confirm="  Are you sure? (y/N): "
if /i not "%confirm%"=="y" goto cancel_reset
echo   Stopping CMA...
docker compose down -v
echo   [ok] Database cleared. Run 'cma' to start fresh.
pause
exit /b 0

:cancel_reset
echo   Cancelled.
pause
exit /b 0
