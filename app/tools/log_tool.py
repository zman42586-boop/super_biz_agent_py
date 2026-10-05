"""本地日志检索工具 - 从 logs/app_*.log 中检索运行日志。"""

from __future__ import annotations

import glob
import hashlib
import os
import re
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from langchain_core.tools import tool
from loguru import logger

from app.tools.result import make_tool_result

_LOGS_DIR = os.path.join(os.getcwd(), "logs")
_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\s*\|\s*(?P<level>\S+)\s*\|"
    r"\s*(?P<location>[^|]+)\|\s*(?P<message>.+)$"
)


@tool
def search_log(query: str = "", minutes: int = 60, limit: int = 100) -> dict[str, Any]:
    """搜索 FastAPI 本地运行日志。

    用于 AIOps 诊断阶段查找最近的错误、异常、超时、工具调用、压缩日志等证据。
    日志来源为项目根目录下的 logs/app_*.log。

    Args:
        query: 关键词，多个词用空格分隔；所有关键词都必须命中。为空时返回时间范围内日志。
        minutes: 向前检索的分钟数，默认最近 60 分钟。
        limit: 最多返回日志条数，默认 100。

    Returns:
        dict: 包含 logs、total、files_scanned、time_range、message 等字段。

    Example:
        用户要求“检查最近 30 分钟 MATLAB 的 ERROR 日志”时，调用：
        search_log(query="MATLAB ERROR", minutes=30, limit=100)
    """
    logger.info(f"本地日志检索工具被调用: query='{query}', minutes={minutes}, limit={limit}")

    now = datetime.now()
    start_dt = now - timedelta(minutes=max(1, minutes))
    limit = max(1, min(limit, 500))

    log_files = sorted(glob.glob(os.path.join(_LOGS_DIR, "app_*.log")))
    if not log_files:
        raw_result = {
            "query": query,
            "total": 0,
            "logs": [],
            "files_scanned": 0,
            "error": f"未找到日志文件: {os.path.join(_LOGS_DIR, 'app_*.log')}",
        }
        return make_tool_result(
            status="error",
            summary=raw_result["error"],
            key_facts={
                "query": query,
                "total": 0,
                "files_scanned": 0,
            },
            raw_result=raw_result,
        )

    keywords = [kw.strip().lower() for kw in query.split() if kw.strip()]
    # Keep the latest matching records so newly appended lines are not hidden
    # behind an already-full result limit.
    logs: deque[dict[str, str | int]] = deque(maxlen=limit)
    files_scanned = 0

    for log_file in log_files:
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                for line_number, raw_line in enumerate(f, start=1):
                    raw_line = raw_line.rstrip("\n")
                    match = _LOG_LINE_RE.match(raw_line)
                    if not match:
                        continue

                    try:
                        log_dt = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue

                    if log_dt < start_dt or log_dt > now:
                        continue

                    level = match.group("level").strip()
                    location = match.group("location").strip()
                    message = match.group("message").strip()
                    full_text = f"{level} {location} {message}".lower()

                    if keywords and not all(keyword in full_text for keyword in keywords):
                        continue

                    logs.append(
                        {
                            "log_id": hashlib.sha256(
                                f"{log_file}:{line_number}:{raw_line}".encode()
                            ).hexdigest()[:20],
                            "source": log_file,
                            "line_number": line_number,
                            "timestamp": match.group("ts"),
                            "level": level,
                            "location": location,
                            "message": message,
                        }
                    )

            files_scanned += 1
        except OSError as exc:
            logger.warning(f"读取日志文件失败: {log_file}, error={exc}")

    raw_result = {
        "query": query,
        "time_range": {
            "start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "end": now.strftime("%Y-%m-%d %H:%M:%S"),
            "minutes": minutes,
        },
        "limit": limit,
        "total": len(logs),
        "logs": list(logs),
        "files_scanned": files_scanned,
        "message": f"找到 {len(logs)} 条匹配日志" if logs else "未找到匹配日志",
    }
    return make_tool_result(
        summary=raw_result["message"],
        key_facts={
            "query": query,
            "time_range": raw_result["time_range"],
            "limit": limit,
            "total": len(logs),
            "files_scanned": files_scanned,
            # Keep the compact evidence list bounded even when limit=500.
            "log_ids": [entry["log_id"] for entry in list(logs)[-100:]],
        },
        raw_result=raw_result,
    )
