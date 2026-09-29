"""MemoryWriter — 长期记忆写入器（Hot + Cold）

在 AIOps 诊断完成后自动调用：
  1. Cold：写入 memory/incidents/{timestamp}_{session_id}.md（完整诊断报告）
  2. Hot：更新 memory/MEMORY.md（指针 + 启发式摘要，供 Agent 每轮加载）

目录说明：
  memory/
  ├── MEMORY.md   — Hot：索引（摘要 + 报告链接）
  ├── incidents/  — Cold：完整诊断报告
  └── artifacts/  — Microcompact 工具原始结果（由 microcompact.py 写入）
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import aiofiles
from loguru import logger

from app.memory.value_judge import decide_review_status, evaluate_memory_value

_MEMORY_ROOT = "memory"
_INCIDENTS_DIR = os.path.join(_MEMORY_ROOT, "incidents")
_ARTIFACTS_DIR = os.path.join(_MEMORY_ROOT, "artifacts")
_MEMORY_MD = os.path.join(_MEMORY_ROOT, "MEMORY.md")
_REVIEW_SUFFIX = ".review.json"

# MEMORY.md 中保留的最近 Incident 条数
_MAX_RECENT_INCIDENTS = 20

# 表格「摘要」列最大字符数（单行，避免撑破表格）
_MAX_SUMMARY_CELL_CHARS = 220


def _ensure_dirs() -> None:
    """确保 Cold / artifacts 目录存在。"""
    for d in [_INCIDENTS_DIR, _ARTIFACTS_DIR]:
        os.makedirs(d, exist_ok=True)


def _sanitize_table_cell(text: str) -> str:
    """表格单元格单行化，并去掉会破坏 pipe 表的字符。"""
    s = " ".join(text.split())
    s = s.replace("|", " ")
    return s.strip()


def _heuristic_summary(task_description: str, report: str) -> str:
    """从任务描述 + 报告正文截取启发式摘要（无 LLM）。"""
    task = _sanitize_table_cell(task_description)[:80]
    body = report.strip()
    # 去掉常见 markdown 标题行，取后续实质内容的一小段
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    snippet_parts: list[str] = []
    for ln in lines:
        if ln.startswith("#"):
            continue
        snippet_parts.append(ln)
        if sum(len(x) for x in snippet_parts) >= 120:
            break
    snippet = _sanitize_table_cell(" ".join(snippet_parts))[:140]

    if task and snippet:
        merged = f"{task} — {snippet}"
    elif task:
        merged = task
    else:
        merged = snippet or "(无摘要)"

    if len(merged) > _MAX_SUMMARY_CELL_CHARS:
        merged = merged[: _MAX_SUMMARY_CELL_CHARS - 1] + "…"
    return merged


class MemoryWriter:
    """AIOps 诊断结果的长期记忆写入器（单例）。"""

    async def save_incident(
        self,
        session_id: str,
        task_description: str,
        report: str,
    ) -> str:
        """留档诊断结果，经 LLM 价值判断后决定入库或转人工审核。

        Args:
            session_id:       AIOps 会话 ID（用作文件名）
            task_description: 原始任务描述
            report:           最终诊断报告 Markdown

        Returns:
            str: 写入的 incident 文件路径
        """
        _ensure_dirs()

        ts = datetime.now()
        safe_session = session_id.replace("/", "_").replace("\\", "_")
        filename = f"{ts.strftime('%Y%m%d_%H%M%S')}_{safe_session}.md"
        incident_path = os.path.join(_INCIDENTS_DIR, filename)

        incident_content = (
            f"# Incident Report\n\n"
            f"- **Session**: {session_id}\n"
            f"- **Time**: {ts.isoformat()}\n"
            f"- **Task**: {task_description}\n\n"
            f"---\n\n"
            f"{report}\n"
        )

        try:
            async with aiofiles.open(incident_path, mode="w", encoding="utf-8") as f:
                await f.write(incident_content)
            logger.info(f"[MemoryWriter] Incident 已写入: {incident_path}")
        except Exception as e:
            logger.error(f"[MemoryWriter] 写入 incident 失败: {e}")
            return ""

        assessment = await evaluate_memory_value(task_description, report)
        review_status = decide_review_status(assessment)
        review_record: dict[str, Any] = {
            "incident": os.path.basename(incident_path),
            "status": review_status,
            "review_type": "llm_judge",
            "created_at": ts.isoformat(),
            "updated_at": ts.isoformat(),
            "assessment": assessment.model_dump() if assessment else None,
            "human_review": None,
        }
        await self._write_review_record(incident_path, review_record)

        summary = _heuristic_summary(task_description, report)
        await self._update_memory_md(
            session_id=session_id,
            summary=summary,
            incident_path=incident_path,
            ts=ts,
            review_status=review_status,
        )

        if review_status == "approved":
            await self._index_incident_to_milvus(incident_path)
        else:
            logger.info(
                f"[MemoryWriter] Incident 状态为 {review_status}，暂不写入 Milvus: {incident_path}"
            )

        return incident_path

    async def _index_incident_to_milvus(self, incident_path: str) -> None:
        """将 incident 报告索引到 Milvus，使后续 retrieve_knowledge 能召回历史经验。

        - 走线程池避免阻塞事件循环（index_single_file 内部是同步 IO + 向量化）
        - 失败仅 warning，不影响 incident 文件落盘与 MEMORY.md 更新
        - 懒导入避免 memory_writer 在启动期强依赖 Milvus
        """
        try:
            from app.services.vector_index_service import vector_index_service

            await asyncio.to_thread(vector_index_service.index_single_file, incident_path)
            logger.info(f"[MemoryWriter] Incident 已索引到 Milvus: {incident_path}")
        except Exception as e:
            logger.warning(f"[MemoryWriter] Incident 索引到 Milvus 失败（不影响主流程）: {e}")

    async def _update_memory_md(
        self,
        session_id: str,
        summary: str,
        incident_path: str,
        ts: datetime,
        review_status: str,
    ) -> None:
        """更新 MEMORY.md 中的 Incident 索引表（Hot）。"""
        link = f"[{os.path.basename(incident_path)}]({incident_path})"
        new_entry = (
            f"| {ts.strftime('%Y-%m-%d %H:%M')} "
            f"| {session_id} "
            f"| {summary} "
            f"| {review_status} "
            f"| {link} |"
        )

        try:
            if os.path.exists(_MEMORY_MD):
                async with aiofiles.open(_MEMORY_MD, encoding="utf-8") as f:
                    existing = await f.read()
            else:
                existing = _MEMORY_MD_TEMPLATE

            lines = existing.splitlines()

            table_start = next(
                (i for i, line in enumerate(lines) if line.startswith("| 时间")), None
            )
            if table_start is not None:
                # 兼容旧版四列表格：旧记录已经进入过 Milvus，迁移时视为 approved。
                if "知识状态" not in lines[table_start]:
                    lines[table_start] = "| 时间 | 会话 ID | 摘要 | 知识状态 | 报告 |"
                    if table_start + 1 < len(lines):
                        lines[table_start + 1] = "|------|---------|------|----------|------|"
                    for index, line in enumerate(lines):
                        if not _is_memory_table_data_row(line):
                            continue
                        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                        if len(cells) == 4:
                            cells.insert(-1, "approved")
                            lines[index] = "| " + " | ".join(cells) + " |"
                insert_pos = table_start + 2
                lines.insert(insert_pos, new_entry)
                data_lines = [line for line in lines if _is_memory_table_data_row(line)]
                if len(data_lines) > _MAX_RECENT_INCIDENTS:
                    other_lines = [line for line in lines if not _is_memory_table_data_row(line)]
                    table_idx = next(
                        (i for i, line in enumerate(other_lines) if line.startswith("| 时间")),
                        None,
                    )
                    if table_idx is not None:
                        lines = (
                            other_lines[: table_idx + 2]
                            + data_lines[:_MAX_RECENT_INCIDENTS]
                            + other_lines[table_idx + 2 :]
                        )
            else:
                lines.append(new_entry)

            updated = "\n".join(lines) + "\n"
            async with aiofiles.open(_MEMORY_MD, mode="w", encoding="utf-8") as f:
                await f.write(updated)

            logger.info("[MemoryWriter] MEMORY.md 已更新")
        except Exception as e:
            logger.error(f"[MemoryWriter] 更新 MEMORY.md 失败: {e}")

    async def _write_review_record(
        self,
        incident_path: str,
        record: dict[str, Any],
    ) -> None:
        review_path = f"{incident_path}{_REVIEW_SUFFIX}"
        async with aiofiles.open(review_path, mode="w", encoding="utf-8") as f:
            await f.write(json.dumps(record, ensure_ascii=False, indent=2))

    def list_reviews(self, status: str | None = None) -> list[dict[str, Any]]:
        """列出知识审核记录，默认返回全部状态。"""
        _ensure_dirs()
        records: list[dict[str, Any]] = []
        for review_path in Path(_INCIDENTS_DIR).glob(f"*{_REVIEW_SUFFIX}"):
            try:
                record = json.loads(review_path.read_text(encoding="utf-8"))
                if status is None or record.get("status") == status:
                    records.append(record)
            except Exception as exc:
                logger.warning(f"[MemoryWriter] 读取审核记录失败 {review_path}: {exc}")
        return sorted(
            records,
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )

    async def human_review(
        self,
        incident_name: str,
        decision: Literal["approved", "rejected"],
        reviewer: str,
        note: str = "",
    ) -> dict[str, Any]:
        """人工批准或拒绝候选记忆；批准后才写入 Milvus。"""
        safe_name = os.path.basename(incident_name)
        if safe_name != incident_name or not safe_name.endswith(".md"):
            raise ValueError("非法 incident 文件名")

        incident_path = os.path.join(_INCIDENTS_DIR, safe_name)
        review_path = f"{incident_path}{_REVIEW_SUFFIX}"
        if not os.path.exists(incident_path) or not os.path.exists(review_path):
            raise FileNotFoundError(safe_name)

        async with aiofiles.open(review_path, encoding="utf-8") as f:
            record = json.loads(await f.read())

        previous_status = record.get("status")
        record["status"] = decision
        record["updated_at"] = datetime.now().isoformat()
        record["human_review"] = {
            "reviewer": reviewer,
            "decision": decision,
            "note": note,
            "reviewed_at": record["updated_at"],
        }
        await self._write_review_record(incident_path, record)
        await self._update_memory_status(safe_name, decision)

        if decision == "approved" and previous_status != "approved":
            await self._index_incident_to_milvus(incident_path)
        return record

    async def _update_memory_status(self, incident_name: str, status: str) -> None:
        """同步更新 MEMORY.md 中对应 Incident 的审核状态。"""
        if not os.path.exists(_MEMORY_MD):
            return
        async with aiofiles.open(_MEMORY_MD, encoding="utf-8") as f:
            lines = (await f.read()).splitlines()
        for index, line in enumerate(lines):
            if incident_name not in line or not _is_memory_table_data_row(line):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 5:
                cells[-2] = status
                lines[index] = "| " + " | ".join(cells) + " |"
            break
        async with aiofiles.open(_MEMORY_MD, mode="w", encoding="utf-8") as f:
            await f.write("\n".join(lines) + "\n")


def _is_memory_table_data_row(line: str) -> bool:
    """识别索引表数据行：以 | 开头且第二个字段像日期时间 (YYYY-MM-DD HH:MM)。"""
    if not line.startswith("|"):
        return False
    # | 2026-05-10 11:20 | ... -> 匹配日期前缀
    return bool(re.match(r"^\|\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*\|", line))


_MEMORY_MD_TEMPLATE = """\
# SuperBizAgent — 活跃记忆索引 (MEMORY.md)

> Hot 层：由系统自动维护。只有 approved 报告可作为正式知识；pending_review 等待人工判断。
> 手动编辑时请保持表格为单行单元格（摘要列勿换行）。

## 最近诊断索引

| 时间 | 会话 ID | 摘要 | 知识状态 | 报告 |
|------|---------|------|----------|------|

## 目录说明

- `incidents/` — Cold：完整诊断报告（自动写入）
- `artifacts/` — Microcompact 工具结果落盘（自动写入，非诊断正文）
"""


# 全局单例
memory_writer = MemoryWriter()
