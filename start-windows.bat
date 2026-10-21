@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PYTHON_CMD=.venv\Scripts\python.exe"
if not exist "%PYTHON_CMD%" (
    echo [ERROR] Run uv sync first.
    exit /b 1
)

echo [1/5] Starting existing MySQL and Milvus services...
docker compose -f vector-database.yml up -d
if errorlevel 1 (
    echo [ERROR] Start Docker Desktop and retry.
    exit /b 1
)

echo [2/5] Starting windows_exporter, local Prometheus and Prometheus MCP...
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-monitoring.ps1
if errorlevel 1 (
    echo [ERROR] Run scripts/install-monitoring.ps1 first or inspect monitoring/runtime logs.
    exit /b 1
)
start "Monitor MCP Server" /min "%PYTHON_CMD%" mcp_servers/monitor_server.py

echo [3/5] Starting FastAPI...
start "SuperBizAgent API" "%PYTHON_CMD%" -m uvicorn app.main:app --host 0.0.0.0 --port 9900
timeout /t 20 /nobreak >nul
curl --noproxy "*" -fsS http://localhost:9900/api/health >nul 2>&1
if errorlevel 1 (
    echo [WARN] FastAPI is still starting. Check http://localhost:9900/docs
) else (
    echo [INFO] Uploading OnCall knowledge docs...
    for %%f in (aiops-docs\*.md) do (
        curl --noproxy "*" -s -X POST http://localhost:9900/api/upload -F "file=@%%f" >nul 2>&1
    )
)

if not defined HARNESS_WORKER_COUNT set "HARNESS_WORKER_COUNT=3"
echo [4/5] Starting !HARNESS_WORKER_COUNT! Harness workers...
for /L %%i in (1,1,!HARNESS_WORKER_COUNT!) do (
    start "Agent Harness Worker %%i" /min "%PYTHON_CMD%" scripts/harness_worker.py
)

echo [5/5] Starting host event agent: process exits, heartbeat, MATLAB logs and Dump...
start "Host Event Agent" /min "%PYTHON_CMD%" scripts/lhm_alert_agent.py
echo.
echo API:            http://localhost:9900
echo Prometheus:     http://127.0.0.1:9090
echo Windows metrics:http://127.0.0.1:9182/metrics
echo Prometheus MCP: http://127.0.0.1:8004/mcp
echo Stop: stop-windows.bat
pause
