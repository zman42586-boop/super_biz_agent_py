@echo off
chcp 65001 >nul 2>&1

echo === Environment Check ===
echo.

if exist .venv\Scripts\python.exe (
    echo [OK] Virtual environment found
) else (
    echo [ERROR] Virtual environment not found, run: python -m uv sync
    pause
    exit /b 1
)

docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker not found
) else (
    echo [OK] Docker OK
)

docker ps 2>nul | findstr "milvus-standalone" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Milvus container not running
) else (
    echo [OK] Milvus running
)

echo.
echo === Check Done ===
pause
