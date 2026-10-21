"""Real LangGraph routing and Replanner with deterministic model boundaries."""

import importlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableLambda

from app.agent.aiops.evidence import make_evidence, register
from app.harness.monitoring import HostMonitoringRepository
from app.harness.repository import HarnessRepository
from app.models.diagnosis import VerificationResult
from app.services.alert_service import AlertService
from tests.test_evidence_verifier import candidate, evidence, state, supported


@pytest.mark.asyncio
@pytest.mark.parametrize("collect_more", [False, True])
async def test_failed_verification_replans_then_passes(monkeypatch, collect_more):
    service = importlib.import_module("app.services.aiops_service")
    verifier = importlib.import_module("app.agent.aiops.verifier")
    replanner = importlib.import_module("app.agent.aiops.replanner")
    monkeypatch.setattr(service.memory_writer, "save_incident", AsyncMock())
    discovery = AsyncMock(return_value=[])
    monkeypatch.setattr(replanner, "get_mcp_tools_with_circuit_breaker", discovery)
    added = make_evidence(kind="incident_fact", source_type="runtime_log", source="allocator.log",
                          locator="line:20", text="Out of memory", context=state()["diagnosis_context"])
    executor_calls = []

    async def executor(current):
        executor_calls.append(current)
        return {"past_steps": current["past_steps"] + [("collect missing", "new evidence")], "plan": [],
                "evidence_registry": register(current["evidence_registry"], [added]),
                "candidate_diagnosis": {}, "candidate_verified": False}

    class Model:
        def with_structured_output(self, schema, **kwargs):
            if schema.__name__ == "Act":
                return RunnableLambda(lambda _: schema(action="replan" if collect_more else "respond",
                    new_steps=["收集 allocator.log"] if collect_more else []))
            if schema.__name__ == "Response":
                return RunnableLambda(lambda _: schema(**candidate(added if collect_more else evidence())))
            raise AssertionError(schema)

    monkeypatch.setattr(service, "executor", executor)
    monkeypatch.setattr(replanner.llm_factory, "create_chat_model", lambda **kwargs: Model())
    semantic = AsyncMock(side_effect=[VerificationResult(status="NON_SUPPORTED", reason="OOM不能证明泄漏",
                                  missing_evidence=["allocator.log 或降低确定性"]), supported()])
    monkeypatch.setattr(verifier, "semantic_check", semantic)
    current = state()
    current["candidate_diagnosis"] = candidate(evidence(), "MATLAB 内存泄漏")
    graph = service.AIOpsService()
    events = [event async for event in graph.execute("diagnose", "alert_eval_route", initial_state=current)]
    assert not [event for event in events if event["type"] == "error"]
    verdicts = [event["verification"]["status"] for event in events if event["type"] == "verification"]
    assert verdicts == ["NON_SUPPORTED", "SUPPORTED"]
    assert len([event for event in events if event["type"] == "report"]) == 1
    report = events[-1]["response"]
    assert "根因：内存分配失败" in report
    assert len(executor_calls) == int(collect_more)
    final = graph.graph.get_state({"configurable": {"thread_id": "alert_eval_route"}}).values
    assert final["verification_attempts"] == 2
    assert len(final["past_steps"]) == 1 + int(collect_more)


@pytest.mark.asyncio
async def test_no_more_evidence_allows_reasoning_only_abstention(monkeypatch):
    service = importlib.import_module("app.services.aiops_service")
    verifier = importlib.import_module("app.agent.aiops.verifier")
    replanner = importlib.import_module("app.agent.aiops.replanner")
    monkeypatch.setattr(service.memory_writer, "save_incident", AsyncMock())
    monkeypatch.setattr(service, "executor", AsyncMock(side_effect=AssertionError("No further tools allowed")))
    monkeypatch.setattr(verifier, "semantic_check", AsyncMock(return_value=VerificationResult(status="NON_SUPPORTED", reason="证据不足")))

    class Model:
        def with_structured_output(self, schema, **kwargs):
            assert schema.__name__ == "Response"
            return RunnableLambda(lambda _: schema(root_cause=None, missing_evidence=["无法获得更多日志"]))

    monkeypatch.setattr(replanner.llm_factory, "create_chat_model", lambda **kwargs: Model())
    current = state()
    current["loop_guard"] = {"reason": "no_progress", "trigger_count": 2}
    graph = service.AIOpsService()
    events = [event async for event in graph.execute("diagnose", "alert_eval_none", initial_state=current)]
    assert events[-1]["type"] == "complete" and "原因未确定" in events[-1]["response"]
    final = graph.graph.get_state({"configurable": {"thread_id": "alert_eval_none"}}).values
    assert final["loop_guard"] == current["loop_guard"]
    assert final["verification_attempts"] == 2


@pytest.mark.asyncio
async def test_recovered_verifier_phase_does_not_execute_leftover_plan(monkeypatch):
    service = importlib.import_module("app.services.aiops_service")
    verifier = importlib.import_module("app.agent.aiops.verifier")
    monkeypatch.setattr(service.memory_writer, "save_incident", AsyncMock())
    monkeypatch.setattr(service, "executor", AsyncMock(side_effect=AssertionError("Wrong recovery phase")))
    monkeypatch.setattr(verifier, "semantic_check", AsyncMock(return_value=supported()))
    current = state()
    current["plan"] = ["old leftover plan"]
    current["verification_attempts"] = 1
    graph = service.AIOpsService()
    events = [event async for event in graph.execute("diagnose", "alert_eval_resume", initial_state=current)]
    assert events[-1]["type"] == "complete"
    final = graph.graph.get_state({"configurable": {"thread_id": "alert_eval_resume"}}).values
    assert final["verification_attempts"] == 2


@pytest.mark.asyncio
async def test_expired_deadline_renders_facts_without_model_or_tools(monkeypatch):
    service = importlib.import_module("app.services.aiops_service")
    monkeypatch.setattr(service.memory_writer, "save_incident", AsyncMock())
    monkeypatch.setattr(service, "evidence_verifier", AsyncMock(side_effect=AssertionError("Expired budget")))
    current = state()
    current["deadline_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    graph = service.AIOpsService()
    events = [event async for event in graph.execute("diagnose", "alert_eval_expired", initial_state=current)]
    assert events[-1]["type"] == "complete"
    assert "累计执行时限耗尽" in events[-1]["response"]
    assert "原因未确定" in events[-1]["response"]


def test_deadline_is_preserved_across_claims_and_manual_resume(tmp_path):
    from app.harness.models import AgentRun

    repository = HarnessRepository(f"sqlite+pysqlite:///{(tmp_path / 'deadline.sqlite').as_posix()}")
    run = repository.create_run(task="diagnose", session_id="deadline")
    repository.claim_next_run("a", 30)
    before, first = repository.reserve_run_deadline(run["id"], 300, worker_id="a", version=1)
    with repository._session_factory.begin() as session:
        session.get(AgentRun, run["id"]).lease_expires_at = repository.db_now(session) - timedelta(seconds=1)
    repository.recover_stale_runs()
    repository.claim_next_run("b", 30)
    after, second = repository.reserve_run_deadline(run["id"], 999, worker_id="b", version=2)
    assert first["deadline_at"] == second["deadline_at"] and after <= before
    repository.close()


@pytest.mark.asyncio
async def test_process_exit_run_freezes_existing_host_history(tmp_path, monkeypatch):
    from tests.test_alert_harness import _alert

    repository = HarnessRepository(f"sqlite+pysqlite:///{(tmp_path / 'alert.sqlite').as_posix()}")
    monitoring = HostMonitoringRepository(repository)
    monitoring.record_heartbeat("workstation-01", {"memory_percent": 90})
    monitoring.record_logs("workstation-01", [{"event_id": "log1", "text": "allocation failed", "source": "diary.log"}])
    monkeypatch.setattr("app.config.config.oncall_auto_diagnosis", True)
    run_id = await AlertService(repository_factory=lambda: repository).trigger_diagnosis(_alert())
    payload = repository.get_run(run_id)["input"]
    assert len(payload["history"]["metrics"]) == 1 and len(payload["history"]["logs"]) == 1
    monitoring.record_heartbeat("workstation-01", {"memory_percent": 20})
    assert len(repository.get_run(run_id)["input"]["history"]["metrics"]) == 1
    repository.close()
