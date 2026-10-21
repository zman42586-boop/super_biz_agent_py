import asyncio
import importlib
from unittest.mock import AsyncMock

import pytest

from app.agent.aiops.evidence import make_evidence, register, seed_evidence
from app.agent.aiops.verifier import (
    deterministic_check, evidence_verifier, final_report,
)
from app.harness.config import harness_settings
from app.harness.repository import HarnessRepository
from app.harness.runtime import HarnessRunContext, harness_run_context
from app.models.diagnosis import CandidateDiagnosis, VerificationResult

CONTEXT = {"run_id": "run", "host_id": "pc", "event_time": "2026-10-20T01:00:00Z"}


def evidence(**kwargs):
    return make_evidence(kind="incident_fact", source_type="runtime_log", source="runtime.log",
                         locator="offset:42", text="Out of memory\nworker.m: allocation failed",
                         context=CONTEXT, event_time="2026-10-20T01:00:00Z", time_basis="observed", **kwargs)


def candidate(row, text="内存分配失败"):
    return CandidateDiagnosis(root_cause=text, claims=[{"text": text, "citations": [
        {"evidence_id": row["evidence_id"], "quote": "Out of memory", "line_start": 1, "line_end": 1}
    ]}]).model_dump()


def state(row=None):
    row = row or evidence()
    return {"input": "诊断", "plan": [], "past_steps": [("collect", "done")], "response": "",
            "steps_summary": "", "phase": "verifier", "diagnosis_context": CONTEXT,
            "evidence_registry": register({}, [row]), "candidate_diagnosis": candidate(row),
            "verification_attempts": 0, "verification_fingerprints": [], "loop_guard": {}}


def supported():
    return VerificationResult(status="SUPPORTED", reason="本次日志支持分配失败，未将其等同泄漏")


@pytest.mark.parametrize("mutation", ["id", "line", "quote", "hash", "host", "run", "time", "inference", "sop"])
def test_bad_provenance_or_unsupported_sop_fails_deterministically(mutation):
    row = evidence()
    raw = candidate(row)
    citation = raw["claims"][0]["citations"][0]
    if mutation == "id": citation["evidence_id"] = "missing"
    if mutation == "line": citation["line_end"] = 99
    if mutation == "quote": citation["quote"] = "memory leak proven"
    if mutation == "hash": row["text"] = "tampered source"
    if mutation == "host": row["host_id"] = "other-pc"
    if mutation == "run": row["run_id"] = "other-run"
    if mutation == "time": row["event_time"] = "2020-01-01T00:00:00Z"
    if mutation == "inference": row["kind"] = "model_inference"
    if mutation == "sop": row["kind"] = "knowledge"
    verdict = deterministic_check(CandidateDiagnosis.model_validate(raw), {row["evidence_id"]: row}, CONTEXT)
    assert verdict.status == "NON_SUPPORTED" and verdict.unsupported_claims


@pytest.mark.asyncio
async def test_first_verification_passes_and_only_then_renders(monkeypatch):
    module = importlib.import_module("app.agent.aiops.verifier")
    check = AsyncMock(return_value=supported())
    monkeypatch.setattr(module, "semantic_check", check)
    current = state()
    update = await evidence_verifier(current)
    assert update["phase"] == "final_report" and update["verification_attempts"] == 1
    report = await final_report({**current, **update})
    assert "根因：内存分配失败" in report["response"]
    assert current["past_steps"] == [("collect", "done")]
    check.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_leak_then_revised_inference_passes_without_new_evidence(monkeypatch):
    module = importlib.import_module("app.agent.aiops.verifier")
    monkeypatch.setattr(module, "semantic_check", AsyncMock(side_effect=[
        VerificationResult(status="NON_SUPPORTED", reason="OOM不能证明泄漏", missing_evidence=["内存增长趋势"]), supported()]))
    current = state()
    current["candidate_diagnosis"] = candidate(evidence(), "内存泄漏")
    update = await evidence_verifier(current)
    assert update["phase"] == "replanner"
    revised = {**current, **update, "candidate_diagnosis": candidate(evidence())}
    second = await evidence_verifier(revised)
    assert second["candidate_verified"] and second["verification_attempts"] == 2
    assert revised["evidence_registry"] == current["evidence_registry"]


@pytest.mark.asyncio
async def test_repeated_candidate_is_stopped_without_another_llm_call(monkeypatch):
    module = importlib.import_module("app.agent.aiops.verifier")
    check = AsyncMock(return_value=VerificationResult(status="NON_SUPPORTED", reason="证据不足"))
    monkeypatch.setattr(module, "semantic_check", check)
    current = state()
    first = await evidence_verifier(current)
    second = await evidence_verifier({**current, **first})
    assert second["phase"] == "final_report" and not second["candidate_verified"]
    assert check.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["step", "tool", "verification"])
async def test_exhausted_budgets_end_safely(monkeypatch, budget):
    module = importlib.import_module("app.agent.aiops.verifier")
    monkeypatch.setattr(module, "semantic_check", AsyncMock(return_value=VerificationResult(status="NON_SUPPORTED", reason="证据不足")))
    current = state()
    if budget == "step": current["past_steps"] *= harness_settings.max_steps
    if budget == "tool": current["tool_calls"] = [{}] * harness_settings.max_tool_calls
    if budget == "verification": current["verification_attempts"] = harness_settings.verification_max_corrections
    result = await evidence_verifier(current)
    assert result["phase"] == "final_report" and not result["candidate_verified"]
    assert "原因未确定" in (await final_report({**current, **result}))["response"]


@pytest.mark.asyncio
async def test_semantic_timeout_is_a_structured_failure(monkeypatch):
    module = importlib.import_module("app.agent.aiops.verifier")
    monkeypatch.setattr(module, "semantic_check", AsyncMock(side_effect=TimeoutError()))
    update = await evidence_verifier(state())
    assert update["verification"]["status"] == "NON_SUPPORTED"
    assert "TimeoutError" in update["verification"]["reason"]


@pytest.mark.asyncio
async def test_published_candidate_cannot_change_after_verification(monkeypatch):
    module = importlib.import_module("app.agent.aiops.verifier")
    monkeypatch.setattr(module, "semantic_check", AsyncMock(return_value=supported()))
    current = state()
    update = await evidence_verifier(current)
    current["candidate_diagnosis"]["root_cause"] = "UNVERIFIED_NEW_CAUSE"
    report = await final_report({**current, **update})
    assert "UNVERIFIED_NEW_CAUSE" not in report["response"]
    assert "原因未确定" in report["response"]


@pytest.mark.asyncio
async def test_reservation_survives_interruption_and_new_worker(tmp_path, monkeypatch):
    from app.harness.models import AgentRun

    module = importlib.import_module("app.agent.aiops.verifier")
    repository = HarnessRepository(f"sqlite+pysqlite:///{(tmp_path / 'recover.sqlite').as_posix()}")
    run = repository.create_run(task="diagnose", session_id="recovery")
    repository.claim_next_run("a", 30)
    current = state()
    context = {**CONTEXT, "run_id": run["id"]}
    row = evidence()
    row["run_id"] = run["id"]
    current.update(diagnosis_context=context, evidence_registry=register({}, [row]))
    monkeypatch.setattr(module, "semantic_check", AsyncMock(side_effect=asyncio.CancelledError()))
    with harness_run_context(HarnessRunContext(run["id"], repository, "a", 1)):
        repository.merge_checkpoint(run["id"], "candidate", current)
        with pytest.raises(asyncio.CancelledError):
            await evidence_verifier(current)
    saved = repository.get_run(run["id"])["state"]
    assert saved["verification_attempts"] == 1 and saved["past_steps"] == [["collect", "done"]]
    from datetime import timedelta

    with repository._session_factory.begin() as session:
        session.get(AgentRun, run["id"]).lease_expires_at = repository.db_now(session) - timedelta(seconds=1)
    repository.recover_stale_runs()
    repository.claim_next_run("b", 30)
    with harness_run_context(HarnessRunContext(run["id"], repository, "b", 2)):
        stopped = await evidence_verifier(saved)
    assert stopped["phase"] == "final_report"  # Same reserved candidate is not replayed.
    assert repository.get_run(run["id"])["state"]["verification_attempts"] == 1
    repository.close()


def test_seeded_logs_keep_collection_time_separate_and_queries_are_not_facts():
    registry = seed_evidence({"task": "I infer a leak", "history": {"logs": [
        {"evidence_id": 7, "received_at": "2026-10-20T01:00:00Z", "payload": {
            "source": "runtime.log", "offset": 20, "ts": "2026-10-20T00:59:00Z", "text": "allocation failed"}}
    ]}}, CONTEXT)
    row = next(iter(registry.values()))
    assert row["time_basis"] == "received" and row["received_at"] != row["event_time"]
    assert "I infer" not in row["text"]


@pytest.mark.asyncio
async def test_gateway_preserves_knowledge_artifact_and_compaction_keeps_registry(tmp_path, monkeypatch):
    from langchain_core.documents import Document
    from langchain_core.tools import tool
    from app.agent.aiops.microcompact import microcompact
    from app.harness.tool_gateway import ToolGateway

    @tool(response_format="content_and_artifact")
    def retrieve_knowledge(query: str):
        """Return an independently identifiable knowledge source."""
        return "summary only", [Document(page_content="Original SOP excerpt" * 100,
                    metadata={"_file_name": "sop.md", "_parent_id": "parent:v1", "_chunk_id": "chunk:v1",
                              "_document_id": "doc", "_document_version": "v1"})]

    result = await ToolGateway().execute(tool=retrieve_knowledge, arguments={"query": "memory"}, evidence_context=CONTEXT)
    assert len(result.evidence) == 1
    assert result.evidence[0]["kind"] == "knowledge" and result.evidence[0]["chunk_id"] == "chunk:v1"
    current = state()
    current["evidence_registry"] = register(current["evidence_registry"], result.evidence)
    before = dict(current["evidence_registry"])
    current["past_steps"] = [("retrieve", "summary " * 1000)]
    # Use the module object, since aiops exports the microcompact callable too.
    module = importlib.import_module("app.agent.aiops.microcompact")
    monkeypatch.setattr(module, "_ARTIFACTS_ROOT", str(tmp_path))
    update = await microcompact(current)
    assert update["past_steps"] != current["past_steps"]
    assert current["evidence_registry"] == before


@pytest.mark.asyncio
async def test_adhoc_tool_budget_counts_planner_prefetch_and_attempts():
    from langchain_core.tools import tool
    from app.harness.loop_guard import LoopGuard, LoopGuardPolicy
    from app.harness.tool_gateway import ToolGateway, ToolExecutionError

    calls = []

    @tool
    def read_fixture(query: str):
        """Read a local synthetic test fixture."""
        calls.append(query)
        return query

    gateway = ToolGateway(guard=LoopGuard(LoopGuardPolicy(max_tool_calls=2)))
    history = []
    await gateway.execute(tool=read_fixture, arguments={"query": "one"}, call_history=history, budget_offset=1)
    with pytest.raises(ToolExecutionError, match="最大工具调用"):
        await gateway.execute(tool=read_fixture, arguments={"query": "two"}, call_history=history, budget_offset=1)
    assert calls == ["one"] and history[0]["started_at"]


@pytest.mark.asyncio
async def test_bounded_prompt_and_report_do_not_crowd_out_runtime_logs():
    from app.agent.aiops.evidence import evidence_context

    rows = [make_evidence(kind="incident_fact", source_type="historical_metric", source=f"metric:{i}",
                         locator="/sample", text="routine memory sample " * 20, context=CONTEXT)
            for i in range(100)]
    rows.append(make_evidence(kind="incident_fact", source_type="runtime_log", source="runtime.log",
                             locator="offset:90", text="CRITICAL_ALLOCATION_FAILURE", context=CONTEXT))
    registry = register({}, rows)
    prompt = evidence_context(registry, max_chars=2000)
    assert "CRITICAL_ALLOCATION_FAILURE" in prompt
    report = await final_report({"evidence_registry": registry})
    assert "CRITICAL_ALLOCATION_FAILURE" in report["response"]
    assert len(registry) == 101
