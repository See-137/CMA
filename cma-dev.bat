@echo off
setlocal enabledelayedexpansion
title CMA - Cost Monitoring Agent (Dev)
cd /d "%~dp0"

echo.
echo   ===================================
echo      $  Cost Monitoring Agent
echo      Dev Mode + RAG Pipeline
echo   ===================================
echo.

:: ── Load .env if it exists ─────────────────────────────────
if exist ".env" (
    echo   [config] Loading .env
    call :load_env
)

:: ── Accept CLI overrides ───────────────────────────────────
:: Usage: cma-dev.bat [openai-key]
if not "%~1"=="" (
    set "CMA_OPENAI_API_KEY=%~1"
    echo   [config] OpenAI key set from CLI arg
)

:: ── Show RAG config ────────────────────────────────────────
if not defined CMA_LLM_PROVIDER set "CMA_LLM_PROVIDER=ollama"
if not defined CMA_LLM_MODEL (
    if /i "!CMA_LLM_PROVIDER!"=="openai" (
        set "CMA_LLM_MODEL=gpt-4o-mini"
    ) else (
        set "CMA_LLM_MODEL=llama3"
    )
)

echo   [config] LLM provider : !CMA_LLM_PROVIDER!
echo   [config] LLM model    : !CMA_LLM_MODEL!
if defined CMA_OPENAI_API_KEY (
    echo   [config] OpenAI key   : !CMA_OPENAI_API_KEY:~0,8!...
    echo   [config] Chat (RAG)   : enabled
) else (
    echo   [config] OpenAI key   : not set
    echo   [config] Chat (RAG)   : semantic search only
    echo.
    echo   TIP: For full RAG chat, run:
    echo     cma-dev.bat sk-proj-YOUR-KEY
    echo   Or create a .env file with CMA_OPENAI_API_KEY=sk-proj-...
)
echo.

:: ── Activate venv ──────────────────────────────────────────
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

:: ── Start Backend ──────────────────────────────────────────
echo   [starting] Backend on http://localhost:8000
start /b "CMA-Backend" cmd /c "cd /d %~dp0backend && set "CMA_LLM_PROVIDER=!CMA_LLM_PROVIDER!" && set "CMA_LLM_MODEL=!CMA_LLM_MODEL!" && set "CMA_OPENAI_API_KEY=!CMA_OPENAI_API_KEY!" && python -m uvicorn app.main:app --reload --port 8000"

echo   [waiting]  Backend health check...
:wait_backend
timeout /t 1 /nobreak >nul
curl -s http://localhost:8000/api/v1/setup/status >nul 2>nul
if %ERRORLEVEL% neq 0 goto wait_backend
echo   [ok]       Backend ready.

:: ── Start Frontend ─────────────────────────────────────────
echo   [starting] Frontend on http://localhost:5173
start /b "CMA-Frontend" cmd /c "cd /d %~dp0frontend && npm run dev"

echo.
echo   ===================================
echo      CMA is running!
echo   ===================================
echo.
echo   Dashboard  : http://localhost:5173
echo   RAG Chat   : http://localhost:5173/chat
echo   Events     : http://localhost:5173/events
echo   API docs   : http://localhost:8000/docs
echo.
echo   Press any key to stop both services...
echo.
pause >nul

echo.
echo   Shutting down...
taskkill /f /fi "WINDOWTITLE eq CMA-Backend" >nul 2>nul
taskkill /f /fi "WINDOWTITLE eq CMA-Frontend" >nul 2>nul
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>nul
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5173 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>nul
echo   [ok] Stopped.
endlocal
exit /b 0

:: ── Subroutine: parse .env safely ──────────────────────────
:load_env
for /f "usebackq eol=# tokens=*" %%L in (".env") do (
    set "%%L"
)
exit /b
