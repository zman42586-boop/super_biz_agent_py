"""时间工具 - 获取当前时间信息"""

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from loguru import logger

from app.tools.result import make_tool_result


@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> dict:
    """获取当前时间

    当用户询问"现在几点"、"今天星期几"、"今天日期"等时间相关问题时，使用此工具。

    Args:
        timezone: 时区，默认为 Asia/Shanghai（北京时间）

    Returns:
        dict: 符合统一 ToolResult 协议的时间信息
    """
    try:
        # 获取指定时区的当前时间
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)

        formatted = now.strftime("%Y-%m-%d %H:%M:%S")
        return make_tool_result(
            summary=f"当前时间为 {formatted}（{timezone}）",
            key_facts={
                "timestamp": now.isoformat(),
                "timezone": timezone,
                "weekday": now.strftime("%A"),
            },
        )

    except Exception as e:
        logger.error(f"时间查询工具调用失败: {e}")
        return make_tool_result(
            status="error",
            summary=f"获取时间失败: {e}",
            key_facts={"timezone": timezone},
        )
