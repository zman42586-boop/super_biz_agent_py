"""Monitor MCP: current host CPU/memory and optional LHM sensors.

Historical evidence is read through the Harness; this server does not generate
past time-series points from a current sample or select a host by service_name.
"""

import logging
import functools
import json
import os
from typing import Dict, Any, Optional
from datetime import UTC, datetime, timedelta
import socket
from urllib.parse import urlparse
from fastmcp import FastMCP

import psutil

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Monitor_MCP_Server")

mcp = FastMCP("Monitor")


def log_tool_call(func):
    """装饰器：记录工具调用的日志，包括方法名、参数和返回状态"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        method_name = func.__name__

        # 记录调用信息
        logger.info("=" * 80)
        logger.info(f"调用方法: {method_name}")

        # 记录参数（排除self等）
        if kwargs:
            # 使用 json.dumps 格式化参数，处理可能的序列化错误
            try:
                params_str = json.dumps(kwargs, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                params_str = str(kwargs)
            logger.info(f"参数信息:\n{params_str}")
        else:
            logger.info("参数信息: 无")

        # 执行方法
        try:
            result = func(*args, **kwargs)

            # 记录返回状态
            logger.info("返回状态: SUCCESS")

            # 记录返回结果摘要（避免日志过长）
            if isinstance(result, dict):
                summary = {k: v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} with {len(v)} items>"
                          for k, v in list(result.items())[:5]}
                logger.info(f"返回结果摘要: {json.dumps(summary, ensure_ascii=False)}")
            else:
                logger.info(f"返回结果: {result}")

            logger.info("=" * 80)
            return result

        except Exception as e:
            # 记录错误状态
            logger.error("返回状态: ERROR")
            logger.error(f"错误信息: {str(e)}")
            logger.error("=" * 80)
            raise

    return wrapper


# ============================================================
# 辅助函数
# ============================================================

def parse_time_or_default(time_str: Optional[str], default_offset_hours: int = 0) -> datetime:
    """解析时间字符串或返回默认时间。

    Args:
        time_str: 时间字符串（格式：YYYY-MM-DD HH:MM:SS）
        default_offset_hours: 默认时间偏移（小时）

    Returns:
        datetime: 解析后的时间对象
    """
    if time_str:
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    # 返回默认时间（当前时间 + 偏移）
    return datetime.now() + timedelta(hours=default_offset_hours)


def generate_time_series(base_time: datetime, minutes_offset: int, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """生成时间序列字符串。

    Args:
        base_time: 基准时间
        minutes_offset: 分钟偏移量
        format_str: 时间格式字符串

    Returns:
        str: 格式化的时间字符串
    """
    result_time = base_time + timedelta(minutes=minutes_offset)
    return result_time.strftime(format_str)





# ============================================================
# 监控数据查询工具
# ============================================================

@mcp.tool()
@log_tool_call
def query_cpu_metrics(
    service_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "1m"
) -> Dict[str, Any]:
    """读取 MCP Server 所在主机当前 CPU；时间范围参数仅保留兼容，不提供历史趋势。"""
    observed = datetime.now(UTC).isoformat()
    value = round(psutil.cpu_percent(interval=0.5), 1)
    return {"service_name": service_name, "host_id": socket.gethostname(),
            "metric_name": "cpu_usage_percent", "data_source": "current_snapshot",
            "observed_at": observed, "current_cpu_percent": value,
            "cpu_count": psutil.cpu_count(logical=True), "interval": interval,
            "requested_range": {"start": start_time, "end": end_time},
            "historical_available": False,
            "message": "仅表示当前主机采样；故障前趋势请读取持久化主机历史证据。",
            "data_points": [{"timestamp": observed, "value": value}],
            "alert_info": {"triggered": value > 80, "threshold": 80.0}}


@mcp.tool()
@log_tool_call
def query_memory_metrics(
    service_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "1m"
) -> Dict[str, Any]:
    """读取 MCP Server 所在主机当前内存；不生成过去时间窗口的数据。"""
    observed = datetime.now(UTC).isoformat()
    memory = psutil.virtual_memory()
    value = round(memory.percent, 1)
    return {"service_name": service_name, "host_id": socket.gethostname(),
            "metric_name": "memory_usage_percent", "data_source": "current_snapshot",
            "observed_at": observed, "current_memory_percent": value,
            "current_used_gb": round(memory.used / (1024 ** 3), 2),
            "total_gb": round(memory.total / (1024 ** 3), 2), "interval": interval,
            "requested_range": {"start": start_time, "end": end_time},
            "historical_available": False,
            "message": "仅表示当前主机采样；不能据此推断故障前内存增长。",
            "data_points": [{"timestamp": observed, "value": value}],
            "alert_info": {"triggered": value > 70, "threshold": 70.0}}


# ============================================================
# LibreHardwareMonitor 真实温度查询工具
# ============================================================

def _lhm_find_sensors(node: dict, results: list) -> None:
    """递归收集所有温度传感器。"""
    children = node.get("Children", [])
    if node.get("Type") == "Temperature":
        try:
            raw = str(node.get("Value", "0")).split()[0].replace(",", ".")
            results.append({
                "id": node.get("id", ""),
                "name": node.get("Text", ""),
                "value": float(raw),
                "parent": node.get("Parent", ""),
            })
        except (ValueError, IndexError):
            pass
    for child in children:
        _lhm_find_sensors(child, results)


def _list_lhm_sensors_impl(lhm_base_url: str = "http://127.0.0.1:8085") -> Dict[str, Any]:
    try:
        import urllib.request
        import json as _json
        url = f"{lhm_base_url}/data.json"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.loads(resp.read())
        results: list = []
        _lhm_find_sensors(data, results)
        return {"total": len(results), "sensors": results}
    except Exception as exc:
        return {
            "total": 0,
            "sensors": [],
            "error": f"无法连接 LibreHardwareMonitor ({lhm_base_url}): {exc}",
        }


@mcp.tool()
@log_tool_call
def list_lhm_sensors(lhm_base_url: str = "http://127.0.0.1:8085") -> Dict[str, Any]:
    """列出 LibreHardwareMonitor 当前所有温度传感器及其实时值。

    Args:
        lhm_base_url: LibreHardwareMonitor Web API 地址，默认 http://127.0.0.1:8085

    Returns:
        Dict:
            - total: 找到的传感器数量
            - sensors: 列表，每项包含 id/name/value(°C)/parent
            - error: 若 LHM 不可达则包含错误说明
    """
    return _list_lhm_sensors_impl(lhm_base_url)


@mcp.tool()
@log_tool_call
def get_lhm_temperature(
    sensor_name_contains: str = "CCDs Max (Tdie)",
    lhm_base_url: str = "http://127.0.0.1:8085",
) -> Dict[str, Any]:
    """获取 LibreHardwareMonitor 中匹配传感器的当前温度值。

    Args:
        sensor_name_contains: 传感器名称关键字（不区分大小写），默认 CCDs Max (Tdie)
        lhm_base_url: LibreHardwareMonitor Web API 地址

    Returns:
        Dict:
            - sensor_name: 传感器名称
            - value_c: 当前温度（°C）
            - timestamp: 查询时间
            - alert_info: 是否超过告警阈值及告警信息（阈值来自环境变量 ONCALL_TEMP_THRESHOLD_C，默认 50）
            - error: 若未找到传感器则包含错误说明
    """
    result = _list_lhm_sensors_impl(lhm_base_url)
    sensors = result.get("sensors", [])
    if result.get("error"):
        return {"error": result["error"]}

    matched = [s for s in sensors if sensor_name_contains.lower() in s["name"].lower()]
    if not matched:
        return {
            "error": f"未找到包含 '{sensor_name_contains}' 的传感器",
            "available_sensors": [s["name"] for s in sensors],
        }

    sensor = matched[0]
    value = sensor["value"]
    try:
        threshold = float(os.getenv("ONCALL_TEMP_THRESHOLD_C", "30"))
    except ValueError:
        threshold = 50.0
    triggered = value >= threshold
    return {
        "host_id": socket.gethostname() if urlparse(lhm_base_url).hostname in {"localhost", "127.0.0.1", "::1"} else "",
        "collector_host_id": socket.gethostname(),
        "observed_at": datetime.now(UTC).isoformat(),
        "data_source": "current_snapshot",
        "sensor_name": sensor["name"],
        "sensor_id": sensor["id"],
        "value_c": value,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "alert_info": {
            "triggered": triggered,
            "threshold": threshold,
            "message": f"温度 {value:.1f}°C 超过 {threshold}°C 阈值" if triggered else f"温度 {value:.1f}°C 正常",
        },
    }

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8004, path="/mcp")
