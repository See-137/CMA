@echo off
setlocal
title CMA Installer - Cost Monitoring Agent
cd /d "%~dp0"

echo.
echo   ===================================
echo      $  Cost Monitoring Agent
echo      Installer
echo   ===================================
echo.

:: ── Check Docker ───────────────────────────────────────────────
where docker >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo   [error] Docker Desktop is required but not installed.
    echo.
    echo   Download it free from:
    echo   https://www.docker.com/products/docker-desktop
    echo.
    echo   Install Docker Desktop, restart your PC, then run this installer again.
    echo.
    pause
    exit /b 1
)
echo   [ok] Docker found

docker info >nul 2>nul
if %ERRORLEVEL% equ 0 goto docker_ok

echo   [info] Docker not running. Starting Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
echo   Waiting for Docker engine...

:wait_docker_install
timeout /t 3 /nobreak >nul
docker info >nul 2>nul
if %ERRORLEVEL% neq 0 goto wait_docker_install
echo   [ok] Docker engine ready.

:docker_ok

set "INSTALL_DIR=%~dp0"
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"
echo   [ok] Install location: %INSTALL_DIR%

:: ── Pre-build Docker images ────────────────────────────────────
echo.
echo   Building CMA (this takes 1-2 minutes on first run)...
echo.
docker compose build
if %ERRORLEVEL% neq 0 (
    echo.
    echo   [error] Docker build failed. Make sure Docker Desktop is running.
    pause
    exit /b 1
)
echo.
echo   [ok] Docker images built

:: ── Create Desktop Shortcut ────────────────────────────────────
echo   Creating desktop shortcut...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([System.IO.Path]::Combine($ws.SpecialFolders('Desktop'), 'CMA - Cost Monitor.lnk')); $s.TargetPath = '%INSTALL_DIR%\cma.bat'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.IconLocation = 'shell32.dll,21'; $s.Description = 'Cost Monitoring Agent'; $s.Save()"
echo   [ok] Desktop shortcut created

:: ── Create Start Menu Shortcut ─────────────────────────────────
echo   Creating Start Menu entry...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $startMenu = [System.IO.Path]::Combine($env:APPDATA, 'Microsoft\Windows\Start Menu\Programs'); $s = $ws.CreateShortcut([System.IO.Path]::Combine($startMenu, 'CMA - Cost Monitor.lnk')); $s.TargetPath = '%INSTALL_DIR%\cma.bat'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.IconLocation = 'shell32.dll,21'; $s.Description = 'Cost Monitoring Agent'; $s.Save()"
echo   [ok] Start Menu entry created

:: ── Done ───────────────────────────────────────────────────────
echo.
echo   ===================================
echo      Installation Complete!
echo   ===================================
echo.
echo   How to use:
echo     - Double-click "CMA - Cost Monitor" on your Desktop
echo     - Or search "CMA" in the Start Menu
echo     - Or run 'cma' from this folder
echo.
echo   First launch will open the setup wizard in your browser.
echo.
set /p launch="  Launch CMA now? (Y/n): "
if /i "%launch%"=="n" goto skip_launch
call "%INSTALL_DIR%\cma.bat"
goto end_install

:skip_launch
echo   OK. Double-click the desktop icon when ready.

:end_install
echo.
pause
exit /b 0
