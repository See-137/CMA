# CMA - Cost Monitoring Agent (Dev Launcher)
# Usage: .\cma-dev.ps1
#   or:  .\cma-dev.ps1 -Key sk-proj-YOUR-KEY

param(
    [string]$Key = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host ""
Write-Host "   ===================================" -ForegroundColor Cyan
Write-Host "      `$  Cost Monitoring Agent"       -ForegroundColor Cyan
Write-Host "      Dev Mode + RAG Pipeline"          -ForegroundColor Cyan
Write-Host "   ===================================" -ForegroundColor Cyan
Write-Host ""

# ── Load .env ───────────────────────────────────────────────
if (Test-Path ".env") {
    Write-Host "   [config] Loading .env" -ForegroundColor DarkGray
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $eqIdx = $line.IndexOf("=")
            if ($eqIdx -gt 0) {
                $k = $line.Substring(0, $eqIdx)
                $v = $line.Substring($eqIdx + 1)
                [Environment]::SetEnvironmentVariable($k, $v, "Process")
            }
        }
    }
}

# ── CLI override ────────────────────────────────────────────
if ($Key) {
    $env:CMA_OPENAI_API_KEY = $Key
    Write-Host "   [config] OpenAI key set from -Key arg" -ForegroundColor DarkGray
}

# ── Defaults ────────────────────────────────────────────────
if (-not $env:CMA_LLM_PROVIDER) { $env:CMA_LLM_PROVIDER = "ollama" }
if (-not $env:CMA_LLM_MODEL) {
    $env:CMA_LLM_MODEL = if ($env:CMA_LLM_PROVIDER -eq "openai") { "gpt-4o-mini" } else { "llama3" }
}

# ── Show config ─────────────────────────────────────────────
Write-Host "   [config] LLM provider : $($env:CMA_LLM_PROVIDER)" -ForegroundColor Gray
Write-Host "   [config] LLM model    : $($env:CMA_LLM_MODEL)" -ForegroundColor Gray

if ($env:CMA_OPENAI_API_KEY) {
    $masked = $env:CMA_OPENAI_API_KEY.Substring(0, [Math]::Min(8, $env:CMA_OPENAI_API_KEY.Length)) + "..."
    Write-Host "   [config] OpenAI key   : $masked" -ForegroundColor Gray
    Write-Host "   [config] Chat (RAG)   : enabled" -ForegroundColor Green
} else {
    Write-Host "   [config] OpenAI key   : not set" -ForegroundColor Yellow
    Write-Host "   [config] Chat (RAG)   : semantic search only" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   TIP: .\cma-dev.ps1 -Key sk-proj-YOUR-KEY" -ForegroundColor DarkGray
}
Write-Host ""

# ── Start Backend ───────────────────────────────────────────
Write-Host "   [starting] Backend on http://localhost:8000" -ForegroundColor White
$backend = Start-Process -PassThru -NoNewWindow -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000" `
    -WorkingDirectory "$root\backend"

Write-Host "   [waiting]  Backend health check..." -ForegroundColor DarkGray
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    $result = curl.exe -s -o NUL -w "%{http_code}" "http://localhost:8000/api/v1/setup/status" 2>$null
    if ($result -eq "200") {
        $ready = $true
        break
    }
}
if (-not $ready) {
    Write-Host "   [error] Backend did not start in 60s" -ForegroundColor Red
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "   [ok]       Backend ready." -ForegroundColor Green

# ── Start Frontend ──────────────────────────────────────────
Write-Host "   [starting] Frontend on http://localhost:5173" -ForegroundColor White
$frontend = Start-Process -PassThru -NoNewWindow -FilePath "cmd.exe" `
    -ArgumentList "/c", "npm", "run", "dev" `
    -WorkingDirectory "$root\frontend"

Write-Host ""
Write-Host "   ===================================" -ForegroundColor Green
Write-Host "      CMA is running!" -ForegroundColor Green
Write-Host "   ===================================" -ForegroundColor Green
Write-Host ""
Write-Host "   Dashboard  : " -NoNewline; Write-Host "http://localhost:5173" -ForegroundColor Cyan
Write-Host "   RAG Chat   : " -NoNewline; Write-Host "http://localhost:5173/chat" -ForegroundColor Cyan
Write-Host "   Events     : " -NoNewline; Write-Host "http://localhost:5173/events" -ForegroundColor Cyan
Write-Host "   API docs   : " -NoNewline; Write-Host "http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "   Press Ctrl+C to stop both services..." -ForegroundColor DarkGray
Write-Host ""

# ── Wait + cleanup on exit ──────────────────────────────────
try {
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host ""
    Write-Host "   Shutting down..." -ForegroundColor Yellow
    if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue }
    if (-not $frontend.HasExited) { Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue }
    # Kill any orphaned processes on those ports
    Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Get-NetTCPConnection -LocalPort 5173 -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Write-Host "   [ok] Stopped." -ForegroundColor Green
}
