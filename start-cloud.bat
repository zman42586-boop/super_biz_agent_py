@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ====================================
echo  SuperBizAgent - Cloud Side Only
echo  (no LHM / no psutil / no Docker)
echo ====================================
echo.

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run: uv sync
    pause
    exit /b 1
)
set PYTHON_CMD=.venv\Scripts\python.exe

echo [1/2] Starting FastAPI (port 9900)...
start "SuperBizAgent Cloud" %PYTHON_CMD% -m uvicorn app.main:app --host 0.0.0.0 --port 9900
echo [OK] FastAPI started
echo.

echo ====================================
echo  Cloud side ready
echo ====================================
echo  API:      http://localhost:9900
echo  Docs:     http://localhost:9900/docs
echo  Heartbeat: POST /api/heartbeat
echo.
echo  Edge: on another machine, set FASTAPI_BASE_URL
echo        and run lhm_alert_agent.py
echo ====================================
pause
