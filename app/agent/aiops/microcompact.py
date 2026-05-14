"""
Level 2 Microcompact 节点

在 executor → replanner 之间运行：
  - 检测最新步骤的工具结果是否超过 token 阈值
  - 超过时将原始结果异步写入 memory/artifacts/{session_id}/ 目录
  - 将 past_steps 中最后一步替换为结构化摘要 + 文件引用
  - 打印 TokenMeter 压缩统计

因 past_steps 使用全量替换 reducer，节点直接返回完整列表。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict

import aiofiles
from loguru import logger

from app.utils.token_meter import count_tokens, log_compression
from .state import PlanExecuteState

# 超过此 token 估算值时触发 Microcompact（演示模式：100 tokens ≈ 400 字符，几乎任何工具结果都会触发）
_MICROCOMPACT_TOKEN_THRESHOLD = 100

# artifacts 根目录
_ARTIFACTS_ROOT = os.path.join("memory", "artifacts")

# 摘要预览参数
_PREVIEW_HEAD_CHARS = 200
_PREVIEW_TAIL_CHARS = 200
_PREVIEW_MAX_ERROR_LINES = 5

# 关键错误行匹配关键字（大小写不敏感）
_ERROR_KEYWORDS = (
    "ERROR",
    "EXCEPTION",
    "TRACEBACK",
    "FAIL",
    "FAILED",
    "CRITICAL",
    "FATAL",
    "WARN",
)


def _extract_error_lines(text: str, max_lines: int) -> list[str]:
    """从文本中按出现顺序抽取关键错误行（去重，保留原始行内容）。"""
    seen: set[str] = set()
    picked: list[str] = []
    for raw_line in text.splitlines():
        upper = raw_line.upper()
        if not any(kw in upper for kw in _ERROR_KEYWORDS):
            continue
        key = raw_line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        picked.append(raw_line)
        if len(picked) >= max_lines:
            break
    return picked


def _smart_preview(
    text: str,
    head: int = _PREVIEW_HEAD_CHARS,
    tail: int = _PREVIEW_TAIL_CHARS,
    max_error_lines: int = _PREVIEW_MAX_ERROR_LINES,
) -> str:
    """生成"头 + 关键错误行 + 尾"的摘要预览。

    - 内容短于 head + tail 时直接返回原文
    - 否则保留头部、尾部，并在中间插入抽取出的错误行（去重）
    """
    if len(text) <= head + tail:
        return text

    parts: list[str] = [text[:head], "...[省略中间]..."]

    error_lines = _extract_error_lines(text, max_error_lines)
    if error_lines:
        parts.append("[关键错误行]")
        parts.extend(error_lines)
        parts.append("...")

    parts.append(text[-tail:])
    return "\n".join(parts)


async def _save_artifact(session_id: str, step: str, content: str) -> str:
    """将原始工具结果异步写入 artifacts 目录，返回相对文件路径。"""
    safe_session = session_id.replace("/", "_").replace("\\", "_")
    dir_path = os.path.join(_ARTIFACTS_ROOT, safe_session)
    os.makedirs(dir_path, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_step = step[:40].replace(" ", "_").replace("/", "_")
    filename = f"{safe_step}_{ts}.txt"
    filepath = os.path.join(dir_path, filename)

    async with aiofiles.open(filepath, mode="w", encoding="utf-8") as f:
        header = f"# Artifact\n# Step: {step}\n# Session: {session_id}\n# Time: {datetime.now().isoformat()}\n\n"
        await f.write(header + content)

    return filepath


async def microcompact(state: PlanExecuteState, config: dict | None = None) -> Dict[str, Any]:
    """Microcompact 节点：压缩超大工具结果，落盘到 artifacts/。"""
    past_steps = list(state.get("past_steps", []))

    if not past_steps:
        return {}

    last_step, last_result = past_steps[-1]
    before_tokens = count_tokens(str(last_result))

    if before_tokens <= _MICROCOMPACT_TOKEN_THRESHOLD:
        return {}

    # 从 LangGraph config 中取 session_id
    session_id = "default"
    if config and isinstance(config, dict):
        session_id = config.get("configurable", {}).get("thread_id", "default")

    try:
        artifact_path = await _save_artifact(session_id, last_step, str(last_result))
    except Exception as e:
        logger.warning(f"[Microcompact] 写入 artifact 失败: {e}")
        artifact_path = "(写入失败)"

    raw_text = str(last_result)
    preview = _smart_preview(raw_text)
    compact_result = (
        f"[Microcompact] 原始结果已压缩落盘 → {artifact_path}\n"
        f"结果摘要（头 {_PREVIEW_HEAD_CHARS} 字符 + 关键错误行 + 尾 {_PREVIEW_TAIL_CHARS} 字符）：\n"
        f"{preview}"
    )

    after_tokens = count_tokens(compact_result)
    log_compression("Microcompact", before_tokens, after_tokens)

    updated_past_steps = past_steps[:-1] + [(last_step, compact_result)]
    return {"past_steps": updated_past_steps}
