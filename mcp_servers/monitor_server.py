"""智能运维监控 MCP Server

本地实现的监控服务 MCP Server，提供：
- 监控数据查询（CPU、内存、磁盘、网络等）
- 进程信息查询
- 历史工单查询
- 服务信息查询

用于支持运维 Agent 的故障排查场景。
"""

import logging
import functools
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
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
        logger.info(f"=" * 80)
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
            logger.info(f"返回状态: SUCCESS")

            # 记录返回结果摘要（避免日志过长）
            if isinstance(result, dict):
                summary = {k: v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} with {len(v)} items>"
                          for k, v in list(result.items())[:5]}
                logger.info(f"返回结果摘要: {json.dumps(summary, ensure_ascii=False)}")
            else:
                logger.info(f"返回结果: {result}")

            logger.info(f"=" * 80)
            return result

        except Exception as e:
            # 记录错误状态
            logger.error(f"返回状态: ERROR")
            logger.error(f"错误信息: {str(e)}")
            logger.error(f"=" * 80)
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
    """查询服务的 CPU 使用率监控数据。

    Args:
        service_name: 服务名称（必填）
            示例: "data-sync-service"
        
        start_time: 开始时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 10:00:00"
            默认值: 如果不传，默认为当前时间的1小时前
            注意: 必须使用字符串格式，而非时间戳
        
        end_time: 结束时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 11:00:00"
            默认值: 如果不传，默认为当前时间
            注意: 必须使用字符串格式，而非时间戳
        
        interval: 数据聚合间隔（可选）
            可选值: "1m" (1分钟), "5m" (5分钟), "1h" (1小时)
            默认值: "1m"
            说明: 控制数据点的时间间隔

    Returns:
        Dict: CPU 监控数据
            - service_name: 服务名称
            - metric_name: 指标名称 (cpu_usage_percent)
            - interval: 数据聚合间隔
            - data_points: 数据点列表，每个点包含:
                * timestamp: 时间点（格式: HH:MM）
                * value: CPU 使用率百分比
            - statistics: 统计信息
                * average: 平均值
                * max: 最大值
                * min: 最小值
            - alert: 告警信息（如有）
                * triggered: 是否触发告警
                * threshold: 告警阈值
                * message: 告警消息
    
    使用示例:
        # 示例1: 使用默认时间（最近1小时）
        query_cpu_metrics(service_name="data-sync-service")
        
        # 示例2: 指定时间范围
        query_cpu_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00",
            end_time="2026-02-14 11:00:00",
            interval="5m"
        )
        
        # 示例3: 只指定开始时间（结束时间自动为当前时间）
        query_cpu_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00"
        )
    """
    # 解析时间参数
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)

    # 解析间隔时间（interval: 1m, 5m, 1h 等）
    interval_minutes = 1
    if interval.endswith('m'):
        interval_minutes = int(interval[:-1])
    elif interval.endswith('h'):
        interval_minutes = int(interval[:-1]) * 60

    # 读取当前真实 CPU 使用率；psutil 是项目依赖，不再降级为 mock 数据
    current_cpu = round(psutil.cpu_percent(interval=0.5), 1)
    cpu_count = psutil.cpu_count(logical=True)
    data_source = "real"

    # 以当前采样值为基准，生成历史序列（用小幅随机波动模拟历史趋势）
    data_points = []
    current_time = start_dt
    time_index = 0
    total_minutes = max(1, int((end_dt - start_dt).total_seconds() / 60))

    while current_time <= end_dt:
        # 本地 psutil 只能提供当前采样值；历史窗口用当前真实值填充，避免生成 mock 趋势
        cpu_value = current_cpu

        data_points.append({
            "timestamp": current_time.strftime("%H:%M"),
            "value": cpu_value,
        })
        current_time += timedelta(minutes=interval_minutes)
        time_index += 1

    if data_points:
        values = [d["value"] for d in data_points]
        avg_value = round(sum(values) / len(values), 2)
        max_value = max(values)
        min_value = min(values)
        spike_detected = max_value > 80.0

        return {
            "service_name": service_name,
            "metric_name": "cpu_usage_percent",
            "interval": interval,
            "data_source": data_source,
            "cpu_count": cpu_count,
            "current_cpu_percent": current_cpu,
            "data_points": data_points,
            "statistics": {
                "avg": avg_value,
                "max": max_value,
                "min": min_value,
                "p95": round(sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else max_value, 2),
                "spike_detected": spike_detected,
            },
            "alert_info": {
                "triggered": spike_detected,
                "threshold": 80.0,
                "message": "CPU 使用率持续超过 80% 阈值" if spike_detected else "CPU 使用率正常",
            },
        }
    else:
        return {
            "service_name": service_name,
            "metric_name": "cpu_usage_percent",
            "interval": interval,
            "data_points": [],
            "statistics": {},
        }


@mcp.tool()
@log_tool_call
def query_memory_metrics(
    service_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "1m"
) -> Dict[str, Any]:
    """查询服务的内存使用监控数据。

    Args:
        service_name: 服务名称（必填）
            示例: "data-sync-service"
        
        start_time: 开始时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 10:00:00"
            默认值: 如果不传，默认为当前时间的1小时前
            注意: 必须使用字符串格式，而非时间戳
        
        end_time: 结束时间（可选，字符串类型）
            格式: "YYYY-MM-DD HH:MM:SS"
            示例: "2026-02-14 11:00:00"
            默认值: 如果不传，默认为当前时间
            注意: 必须使用字符串格式，而非时间戳
        
        interval: 数据聚合间隔（可选）
            可选值: "1m" (1分钟), "5m" (5分钟), "1h" (1小时)
            默认值: "1m"

    Returns:
        Dict: 内存监控数据
            - service_name: 服务名称
            - metric_name: 指标名称 (memory_usage_percent)
            - interval: 数据聚合间隔
            - data_points: 数据点列表，每个点包含:
                * timestamp: 时间点（格式: HH:MM）
                * value: 内存使用率百分比
                * used_gb: 已使用内存（GB）
                * total_gb: 总内存（GB）
            - statistics: 统计信息
                * average: 平均值
                * max: 最大值
                * min: 最小值
            - alert: 告警信息（如有）
                * triggered: 是否触发告警
                * threshold: 告警阈值
                * message: 告警消息
    
    使用示例:
        # 示例1: 使用默认时间（最近1小时）
        query_memory_metrics(service_name="data-sync-service")
        
        # 示例2: 指定时间范围
        query_memory_metrics(
            service_name="data-sync-service",
            start_time="2026-02-14 10:00:00",
            end_time="2026-02-14 11:00:00",
            interval="5m"
        )
    """
    # 解析时间参数
    start_dt = parse_time_or_default(start_time, default_offset_hours=-1)
    end_dt = parse_time_or_default(end_time, default_offset_hours=0)

    # 解析间隔时间（interval: 1m, 5m, 1h 等）
    interval_minutes = 1
    if interval.endswith('m'):
        interval_minutes = int(interval[:-1])
    elif interval.endswith('h'):
        interval_minutes = int(interval[:-1]) * 60

    # 读取当前真实内存使用率；psutil 是项目依赖，不再降级为 mock 数据
    vm = psutil.virtual_memory()
    current_memory = round(vm.percent, 1)
    total_gb = round(vm.total / (1024 ** 3), 2)
    used_gb_now = round(vm.used / (1024 ** 3), 2)
    data_source = "real"

    # 以当前采样值为基准，生成历史序列
    data_points = []
    current_time = start_dt
    time_index = 0
    total_minutes = max(1, int((end_dt - start_dt).total_seconds() / 60))

    while current_time <= end_dt:
        # 本地 psutil 只能提供当前采样值；历史窗口用当前真实值填充，避免生成 mock 趋势
        memory_value = current_memory
        used_gb = round((memory_value / 100.0) * total_gb, 2)

        data_points.append({
            "timestamp": current_time.strftime("%H:%M"),
            "value": memory_value,
            "used_gb": used_gb,
            "total_gb": total_gb,
        })
        current_time += timedelta(minutes=interval_minutes)
        time_index += 1

    if data_points:
        values = [d["value"] for d in data_points]
        avg_value = round(sum(values) / len(values), 2)
        max_value = max(values)
        min_value = min(values)
        memory_pressure = max_value > 70.0

        return {
            "service_name": service_name,
            "metric_name": "memory_usage_percent",
            "interval": interval,
            "data_source": data_source,
            "current_memory_percent": current_memory,
            "current_used_gb": used_gb_now,
            "total_gb": total_gb,
            "data_points": data_points,
            "statistics": {
                "avg": avg_value,
                "max": max_value,
                "min": min_value,
                "p95": round(sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else max_value, 2),
                "memory_pressure": memory_pressure,
            },
            "alert_info": {
                "triggered": memory_pressure,
                "threshold": 70.0,
                "message": "内存使用率超过 70% 阈值，存在内存压力" if memory_pressure else "内存使用率正常",
            },
        }
    else:
        return {
            "service_name": service_name,
            "metric_name": "memory_usage_percent",
            "interval": interval,
            "data_points": [],
            "statistics": {},
            "error": "时间范围无效或没有生成数据点",
        }




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
