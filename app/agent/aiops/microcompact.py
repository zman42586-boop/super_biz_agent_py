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

import json
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import aiofiles
from loguru import logger

from app.tools.result import is_tool_result
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


async def microcompact(state: PlanExecuteState, config: dict | None = None) -> dict[str, Any]:
    """Microcompact 节点：压缩超大工具结果，落盘到 artifacts/。"""
    past_steps = list(state.get("past_steps", []))

    if not past_steps:
        return {}

    last_step, last_result = past_steps[-1]
    serialized_result = (
        json.dumps(last_result, ensure_ascii=False, indent=2, default=str)
        if isinstance(last_result, Mapping)
        else str(last_result)
    )
    before_tokens = count_tokens(serialized_result)

    if before_tokens <= _MICROCOMPACT_TOKEN_THRESHOLD:
        return {}

    # 从 LangGraph config 中取 session_id
    session_id = "default"
    if config and isinstance(config, dict):
        session_id = config.get("configurable", {}).get("thread_id", "default")

    structured_result: dict[str, Any] | None = None
    if is_tool_result(last_result):
        structured_result = dict(last_result)
    elif isinstance(last_result, str):
        try:
            parsed = json.loads(last_result)
        except (TypeError, ValueError):
            parsed = None
        if is_tool_result(parsed):
            structured_result = dict(parsed)

    # 新协议：Tool 已经声明 summary/key_facts，Microcompact 不再猜业务关键词。
    if structured_result is not None:
        raw_result = structured_result.get("raw_result", structured_result)
        raw_text = (
            raw_result
            if isinstance(raw_result, str)
            else json.dumps(raw_result, ensure_ascii=False, indent=2, default=str)
        )
        artifact_path = structured_result.get("raw_ref")
        if not artifact_path:
            try:
                artifact_path = await _save_artifact(session_id, last_step, raw_text)
            except Exception as e:
                logger.warning(f"[Microcompact] 写入 artifact 失败: {e}")
                # 原文没有可靠落盘时不删除 raw_result，避免压缩造成不可恢复的数据丢失。
                return {}

        compact_result = {
            key: value for key, value in structured_result.items() if key != "raw_result"
        }
        compact_result["raw_ref"] = artifact_path
        compact_result["truncated"] = True

        after_tokens = count_tokens(json.dumps(compact_result, ensure_ascii=False, default=str))
        log_compression("Microcompact", before_tokens, after_tokens)
        updated_past_steps = past_steps[:-1] + [(last_step, compact_result)]
        return {"past_steps": updated_past_steps}

    # 旧工具兼容：纯文本仍使用“头 + 错误行 + 尾”的确定性兜底规则。
    try:
        artifact_path = await _save_artifact(session_id, last_step, serialized_result)
    except Exception as e:
        logger.warning(f"[Microcompact] 写入 artifact 失败: {e}")
        artifact_path = "(写入失败)"

    raw_text = serialized_result
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
