@echo off
chcp 65001 >nul
echo ====================================
echo 停止 SuperBizAgent 服务
echo ====================================
echo.

REM 停止 FastAPI 服务
echo [1/5] 停止 FastAPI 服务...
taskkill /FI "WINDOWTITLE eq SuperBizAgent API*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] FastAPI 服务未运行或已停止
) else (
    echo [成功] FastAPI 服务已停止
)
echo.

REM 停止 Monitor MCP 服务
echo [2/5] 停止 Monitor MCP 服务...
taskkill /FI "WINDOWTITLE eq Monitor MCP Server*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] Monitor MCP 服务未运行或已停止
) else (
    echo [成功] Monitor MCP 服务已停止
)
echo.

REM 停止 Agent Harness Worker
echo [3/5] 停止 Agent Harness Worker...
taskkill /FI "WINDOWTITLE eq Agent Harness Worker*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] Agent Harness Worker 未运行或已停止
) else (
    echo [成功] Agent Harness Worker 已停止
)
echo.

REM 停止 LHM Alert Agent
echo [4/5] 停止 LHM Alert Agent...
taskkill /FI "WINDOWTITLE eq LHM Alert Agent*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] LHM Alert Agent 未运行或已停止
) else (
    echo [成功] LHM Alert Agent 已停止
)
echo.

REM 停止 Docker 容器
echo [5/5] 停止 MySQL 和 Milvus 容器...
docker ps --format "{{.Names}}" | findstr /R "milvus superbiz-mysql" >nul 2>&1
if not errorlevel 1 (
    docker compose -f vector-database.yml down
    if errorlevel 1 (
        echo [错误] Docker 容器停止失败
    ) else (
        echo [成功] MySQL 和 Milvus 容器已停止
    )
) else (
    echo [信息] MySQL 和 Milvus 容器未运行
)
echo.

echo ====================================
echo 所有服务已停止！
echo ====================================
echo.
echo 提示:
echo   - 如需完全清理 Docker 数据卷，运行:
echo     docker compose -f vector-database.yml down -v
echo.
pause
