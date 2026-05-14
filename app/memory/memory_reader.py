"""MemoryReader — 长期记忆读取器（Hot 层）

供 Planner / RAG Agent 调用，将 Hot 记忆注入到 Prompt 中。

读取范围：
  - memory/MEMORY.md  — 最近诊断索引 + 启发式摘要 + 指向 Cold 报告的链接（由 memory_writer 维护）

不读取（Cold / 归档，避免撑爆上下文）：
  - memory/incidents/  — 完整诊断报告
  - memory/artifacts/  — Microcompact 工具结果落盘
"""
from __future__ import annotations

import os

from loguru import logger

_MEMORY_ROOT = "memory"
_MEMORY_MD = os.path.join(_MEMORY_ROOT, "MEMORY.md")

# 注入 Prompt 的字符上限，防止撑爆上下文窗口
_DEFAULT_MAX_CHARS = 4000


def load_memory_context(max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """读取 MEMORY.md，返回用于注入的上下文字符串。

    Args:
        max_chars: 最大字符数限制

    Returns:
        str: 格式化的长期记忆上下文；若文件不存在或为空则返回空字符串
    """
    if not os.path.exists(_MEMORY_MD):
        return ""

    try:
        with open(_MEMORY_MD, encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        logger.warning(f"[MemoryReader] 读取 MEMORY.md 失败: {e}")
        return ""

    if not content:
        return ""

    combined = f"### 历史诊断索引 (MEMORY.md)\n\n{content}"

    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n\n...(长期记忆已截断)"
        logger.info(f"[MemoryReader] 记忆上下文超出 {max_chars} 字符，已截断")
    else:
        logger.info(f"[MemoryReader] 加载长期记忆上下文，共 {len(combined)} 字符")

    return combined
