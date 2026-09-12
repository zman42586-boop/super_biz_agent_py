"""Durable Agent Run APIs, including resumable SSE event delivery."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.harness.repository import TERMINAL_RUN_STATUSES, get_harness_repository

router = APIRouter()


class CreateRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20_000)
    session_id: str | None = Field(default=None, max_length=128)
    kind: str = Field(default="diagnosis", pattern=r"^[a-z0-9_-]{1,32}$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecordEvaluationRequest(BaseModel):
    experiment_name: str = Field(default="online", min_length=1, max_length=128)
    evaluator_name: str = Field(min_length=1, max_length=128)
    score: float = Field(ge=0, le=10)
    passed: bool
    metrics: dict[str, float] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=4000)


async def _repository_call(method_name: str, *args, **kwargs):
    try:
        repository = await asyncio.to_thread(get_harness_repository)
        method = getattr(repository, method_name)
        return await asyncio.to_thread(method, *args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Harness database unavailable: {exc}",
        ) from exc


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(request: CreateRunRequest) -> dict:
    session_id = request.session_id or f"run-session-{uuid.uuid4().hex[:12]}"
    return await _repository_call(
        "create_run",
        task=request.task,
        session_id=session_id,
        kind=request.kind,
        payload={"metadata": request.metadata},
    )


@router.get("/runs/metrics/summary")
async def get_metrics_summary(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    kind: str | None = Query(default=None, pattern=r"^[a-z0-9_-]{1,32}$"),
) -> dict:
    """Return online reliability, latency and quality metrics for a fixed window."""
    return await _repository_call(
        "get_metrics_summary", window_hours=window_hours, kind=kind
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = await _repository_call("get_run", run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/steps")
async def list_run_steps(run_id: str) -> dict:
    run = await _repository_call("get_run", run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    steps, tool_calls = await asyncio.gather(
        _repository_call("list_steps", run_id),
        _repository_call("list_tool_calls", run_id),
    )
    calls_by_step: dict[int, list[dict]] = {}
    for call in tool_calls:
        calls_by_step.setdefault(int(call["step_id"]), []).append(call)
    for step in steps:
        step["tool_calls"] = calls_by_step.get(int(step["id"]), [])
    return {"run_id": run_id, "steps": steps}


@router.get("/runs/{run_id}/evaluations")
async def list_run_evaluations(run_id: str) -> dict:
    run = await _repository_call("get_run", run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    evaluations = await _repository_call("list_evaluations", run_id)
    return {"run_id": run_id, "evaluations": evaluations}


@router.post("/runs/{run_id}/evaluations", status_code=status.HTTP_201_CREATED)
async def record_run_evaluation(run_id: str, request: RecordEvaluationRequest) -> dict:
    run = await _repository_call("get_run", run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return await _repository_call(
        "record_evaluation",
        run_id,
        experiment_name=request.experiment_name,
        evaluator_name=request.evaluator_name,
        score=request.score,
        passed=request.passed,
        metrics=request.metrics,
        comment=request.comment,
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    run = await _repository_call("cancel_run", run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/runs/{run_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_run(run_id: str) -> dict:
    run = await _repository_call("resume_run", run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    run = await _repository_call("get_run", run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError:
        cursor = after

    async def event_generator():
        nonlocal cursor
        idle_after_terminal = 0
        while True:
            events = await _repository_call("list_events", run_id, cursor, 200)
            for event in events:
                cursor = int(event["id"])
                yield {
                    "id": str(cursor),
                    "event": event["type"],
                    "data": json.dumps(event, ensure_ascii=False),
                }

            current = await _repository_call("get_run", run_id)
            if current is None:
                break
            if current["status"] in TERMINAL_RUN_STATUSES and not events:
                idle_after_terminal += 1
                if idle_after_terminal >= 2:
                    break
            else:
                idle_after_terminal = 0
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())
