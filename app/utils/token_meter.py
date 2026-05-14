"""TokenMeter — 上下文压缩可观测性工具

使用字符估算 token 数（约 4 字符/token，适用于中英混合文本）。
在每个压缩阶段前后打印统计信息，格式：
  [Snip        ] before=3200  after=800   reduced=2400  ratio=75.0%
"""

from __future__ import annotations

from typing import Sequence

from langchain_core.messages import BaseMessage
from loguru import logger


# 字符/token 估算比例（中英混合约 3-4 字符/token）
_CHARS_PER_TOKEN = 4


def count_tokens(text: str) -> int:
    """估算单段文本的 token 数。"""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def count_state_tokens(messages: Sequence) -> int:
    """估算消息列表的总 token 数。"""
    total = 0
    for msg in messages:
        if isinstance(msg, BaseMessage):
            content = msg.content
        else:
            content = str(msg)
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        total += count_tokens(str(content))
    return total


def count_steps_tokens(past_steps: list[tuple]) -> int:
    """估算 past_steps 列表的总 token 数。"""
    total = 0
    for step, result in past_steps:
        total += count_tokens(str(step)) + count_tokens(str(result))
    return total


def log_compression(stage: str, before: int, after: int) -> None:
    """在终端打印压缩前后的 token 统计。

    输出格式（对齐）：
      [Microcompact] before=18420 after=980   reduced=17440 ratio=94.7%
    """
    reduced = before - after
    ratio = (reduced / before * 100) if before > 0 else 0.0
    logger.info(
        f"[{stage:<12}] before={before:<6} after={after:<6} "
        f"reduced={reduced:<6} ratio={ratio:.1f}%"
    )
