@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ====================================
echo  SuperBizAgent - Starting Services
echo ====================================
echo.

REM Check virtual environment
if not exist .venv\Scripts\python.exe (
    echo [ERROR] Virtual environment not found.
    echo [INFO]  Please run: python -m uv sync
    pause
    exit /b 1
)
set PYTHON_CMD=.venv\Scripts\python.exe
set "LHM_EXE=LibreHardwareMonitor\LibreHardwareMonitor.exe"
echo [OK] Virtual environment ready
echo.

REM Start Docker Compose
echo [1/5] Starting Milvus (Docker)...
docker ps --format "{{.Names}}" 2>nul | findstr "milvus-standalone" >nul 2>&1
if not errorlevel 1 (
    echo [OK] Milvus already running
) else (
    echo [INFO] Starting Milvus containers...
    docker compose -f vector-database.yml up -d
    if errorlevel 1 (
        echo [ERROR] Docker failed. Make sure Docker Desktop is running.
        pause
        exit /b 1
    )
    echo [INFO] Waiting 15s for Milvus to start...
    timeout /t 15 /nobreak >nul
)
echo [OK] Milvus ready
echo.

REM Start LibreHardwareMonitor
echo [2/5] Starting LibreHardwareMonitor...
tasklist /FI "IMAGENAME eq LibreHardwareMonitor.exe" 2>nul | findstr /I "LibreHardwareMonitor.exe" >nul 2>&1
if not errorlevel 1 (
    echo [OK] LibreHardwareMonitor already running
) else (
    if exist "%LHM_EXE%" (
        echo [INFO] Starting LibreHardwareMonitor...
        start "LibreHardwareMonitor" /min "%LHM_EXE%"
        timeout /t 3 /nobreak >nul
        echo [OK] LibreHardwareMonitor started
    ) else (
        echo [WARN] LibreHardwareMonitor executable not found: %LHM_EXE%
        echo [WARN] LHM Alert Agent needs LibreHardwareMonitor Web Server at LHM_BASE_URL.
    )
)
echo.

REM Start Monitor MCP Server
echo [3/5] Starting Monitor MCP Server (port 8004)...
start "Monitor MCP Server" /min %PYTHON_CMD% mcp_servers/monitor_server.py
timeout /t 2 /nobreak >nul
echo [OK] Monitor MCP Server started
echo.

REM Start FastAPI
echo [4/5] Starting FastAPI (port 9900)...
start "SuperBizAgent API" %PYTHON_CMD% -m uvicorn app.main:app --host 0.0.0.0 --port 9900
echo [INFO] Waiting 20s for service to start...
timeout /t 20 /nobreak >nul
echo.

REM Health check and upload docs
echo [INFO] Checking service health...
curl -s http://localhost:9900/api/health >nul 2>&1
if errorlevel 1 (
    echo [WARN] Service may still be starting. Try http://localhost:9900 in a moment.
) else (
    echo [OK] FastAPI is running
    echo.
    echo [INFO] Uploading OnCall knowledge docs...
    for %%f in (aiops-docs\*.md) do (
        echo   Uploading: %%~nxf
        curl -s -X POST http://localhost:9900/api/upload -F "file=@%%f" >nul 2>&1
    )
    echo [OK] Docs uploaded
)

REM Start LHM Alert Agent after FastAPI is ready
echo.
echo [5/5] Starting LHM Alert Agent (active OnCall detector)...
start "LHM Alert Agent" /min %PYTHON_CMD% scripts/lhm_alert_agent.py
timeout /t 2 /nobreak >nul
echo [OK] LHM Alert Agent started
echo [INFO] Make sure LibreHardwareMonitor Web Server is running at LHM_BASE_URL.

echo.
echo ====================================
echo  All services started!
echo ====================================
echo  Web UI:    http://localhost:9900
echo  API Docs:  http://localhost:9900/docs
echo  Milvus UI: http://localhost:8000
echo  Monitor MCP: http://localhost:8004/mcp
echo  LibreHardwareMonitor: http://127.0.0.1:8085
echo  LHM Agent: active temperature detector
echo.
echo  To stop:   stop-windows.bat
echo ====================================
pause
