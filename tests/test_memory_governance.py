import importlib
import json
from pathlib import Path

import pytest

from app.memory.memory_reader import _approved_memory_only
from app.memory.memory_writer import MemoryWriter
from app.memory.value_judge import MemoryValueAssessment, decide_review_status


def _assessment(score: float, *, ambiguous: bool = False, risk: str = "low"):
    return MemoryValueAssessment(
        knowledge_value_score=score,
        ambiguous=ambiguous,
        risk_level=risk,
        reason="测试评估",
    )


def test_memory_value_gate_routes_clear_and_ambiguous_cases():
    assert decide_review_status(_assessment(0.9)) == "approved"
    assert decide_review_status(_assessment(0.2)) == "rejected"
    assert decide_review_status(_assessment(0.7)) == "pending_review"
    assert decide_review_status(_assessment(0.9, ambiguous=True)) == "pending_review"
    assert decide_review_status(_assessment(0.9, risk="high")) == "pending_review"
    assert decide_review_status(None) == "pending_review"


@pytest.mark.asyncio
async def test_high_value_incident_is_archived_and_indexed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    async def fake_evaluate(*_args):
        return _assessment(0.92)

    indexed: list[str] = []

    async def fake_index(path: str):
        indexed.append(path)

    writer_module = importlib.import_module("app.memory.memory_writer")
    monkeypatch.setattr(writer_module, "evaluate_memory_value", fake_evaluate)
    writer = MemoryWriter()
    monkeypatch.setattr(writer, "_index_incident_to_milvus", fake_index)

    incident_path = await writer.save_incident("run-1", "排查 OOM", "日志确认 OOM，建议分块读取。")

    assert Path(incident_path).exists()
    assert indexed == [incident_path]
    review = json.loads(Path(f"{incident_path}.review.json").read_text(encoding="utf-8"))
    assert review["status"] == "approved"
    assert "| approved |" in Path("memory/MEMORY.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_ambiguous_incident_waits_for_human_approval(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    async def fake_evaluate(*_args):
        return _assessment(0.9, ambiguous=True)

    indexed: list[str] = []

    async def fake_index(path: str):
        indexed.append(path)

    writer_module = importlib.import_module("app.memory.memory_writer")
    monkeypatch.setattr(writer_module, "evaluate_memory_value", fake_evaluate)
    writer = MemoryWriter()
    monkeypatch.setattr(writer, "_index_incident_to_milvus", fake_index)

    incident_path = await writer.save_incident("run-2", "排查崩溃", "可能是驱动问题。")
    incident_name = Path(incident_path).name

    assert indexed == []
    assert writer.list_reviews("pending_review")[0]["incident"] == incident_name

    reviewed = await writer.human_review(
        incident_name,
        decision="approved",
        reviewer="alice",
        note="已核对原始日志",
    )

    assert reviewed["status"] == "approved"
    assert reviewed["human_review"]["reviewer"] == "alice"
    assert indexed == [incident_path]
    assert "| approved |" in Path("memory/MEMORY.md").read_text(encoding="utf-8")


def test_unapproved_memory_is_not_loaded_into_prompt():
    content = """| 时间 | 会话 ID | 摘要 | 知识状态 | 报告 |
|------|---------|------|----------|------|
| 2026-09-21 10:00 | a | 可信案例 | approved | [a](a.md) |
| 2026-09-21 10:01 | b | 待审核案例 | pending_review | [b](b.md) |
| 2026-09-21 10:02 | c | 无效案例 | rejected | [c](c.md) |"""

    filtered = _approved_memory_only(content)

    assert "可信案例" in filtered
    assert "待审核案例" not in filtered
    assert "无效案例" not in filtered
