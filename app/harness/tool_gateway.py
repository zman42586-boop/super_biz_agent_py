"""Reliable, observable and idempotent gateway for every Agent tool call."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.harness.config import harness_settings
from app.harness.runtime import get_run_context


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ToolPolicy:
    timeout_seconds: float = harness_settings.tool_timeout_seconds
    max_retries: int = harness_settings.tool_max_retries
    read_only: bool = True
    max_output_chars: int = 10_000


@dataclass(frozen=True)
class ToolExecutionResult:
    content: str
    tool_name: str
    attempts: int
    latency_ms: int
    cached: bool = False
    artifact_path: str | None = None


def _safe_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if hasattr(result, "content"):
        return _result_text(result.content)
    if isinstance(result, tuple) and result:
        return _result_text(result[0])
    return json.dumps(_safe_json(result), ensure_ascii=False, indent=2)


def _validate_arguments(tool: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return arguments
    try:
        if hasattr(schema, "model_validate"):
            parsed = schema.model_validate(arguments)
            return parsed.model_dump()
        if hasattr(schema, "parse_obj"):
            parsed = schema.parse_obj(arguments)
            return parsed.dict()
        return arguments
    except Exception as exc:
        raise ToolExecutionError("INVALID_ARGUMENTS", str(exc), retryable=False) from exc


def _classify_error(exc: BaseException) -> ToolExecutionError:
    if isinstance(exc, ToolExecutionError):
        return exc
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return ToolExecutionError("TIMEOUT", str(exc) or "Tool call timed out", retryable=True)
    name = type(exc).__name__.lower()
    message = str(exc)
    retryable_markers = (
        "connection",
        "timeout",
        "temporarily",
        "rate limit",
        "429",
        "502",
        "503",
    )
    retryable = any(marker in f"{name} {message}".lower() for marker in retryable_markers)
    return ToolExecutionError(
        "TOOL_UNAVAILABLE" if retryable else "TOOL_EXECUTION_FAILED",
        message or type(exc).__name__,
        retryable=retryable,
    )


class ToolGateway:
    def __init__(self, policies: dict[str, ToolPolicy] | None = None) -> None:
        self.policies = policies or {}

    async def execute(
        self,
        *,
        tool: Any,
        arguments: dict[str, Any],
        step_id: int | None = None,
        step_index: int = 0,
    ) -> ToolExecutionResult:
        tool_name = str(getattr(tool, "name", getattr(tool, "__name__", "unknown")))
        policy = self.policies.get(tool_name, ToolPolicy())
        validated = _validate_arguments(tool, arguments)
        context = get_run_context()
        idempotency_key = self._idempotency_key(
            context.run_id if context else "adhoc", step_index, tool_name, validated
        )

        call_row: dict[str, Any] | None = None
        if context is not None and step_id is not None:
            call_row, cached = await asyncio.to_thread(
                context.repository.begin_tool_call,
                run_id=context.run_id,
                step_id=step_id,
                tool_name=tool_name,
                arguments=validated,
                idempotency_key=idempotency_key,
            )
            if cached:
                result = call_row.get("result") or {}
                return ToolExecutionResult(
                    content=str(result.get("content", "")),
                    tool_name=tool_name,
                    attempts=int(call_row.get("attempt_count") or 1),
                    latency_ms=int(call_row.get("latency_ms") or 0),
                    cached=True,
                    artifact_path=call_row.get("result_artifact_path"),
                )

        attempts = max(1, policy.max_retries + 1)
        total_started = time.perf_counter()
        last_error: ToolExecutionError | None = None
        call_id = int(call_row["id"]) if call_row else None

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                raw_result = await asyncio.wait_for(
                    self._invoke(tool, validated), timeout=policy.timeout_seconds
                )
                content, artifact_path = await asyncio.to_thread(
                    self._limit_result,
                    context.run_id if context else "adhoc",
                    idempotency_key,
                    _result_text(raw_result),
                    policy.max_output_chars,
                )
                latency_ms = int((time.perf_counter() - total_started) * 1000)
                if context is not None and call_id is not None:
                    await asyncio.to_thread(
                        context.repository.complete_tool_call,
                        call_id,
                        result={"content": content},
                        latency_ms=latency_ms,
                        artifact_path=artifact_path,
                    )
                return ToolExecutionResult(
                    content=content,
                    tool_name=tool_name,
                    attempts=attempt,
                    latency_ms=latency_ms,
                    artifact_path=artifact_path,
                )
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                last_error = _classify_error(exc)
                terminal = not last_error.retryable or attempt >= attempts
                if context is not None and call_id is not None:
                    await asyncio.to_thread(
                        context.repository.fail_tool_call,
                        call_id,
                        error_code=last_error.code,
                        error_message=str(last_error),
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        terminal=terminal,
                    )
                if terminal:
                    raise last_error from exc
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                if context is not None and step_id is not None:
                    call_row, cached = await asyncio.to_thread(
                        context.repository.begin_tool_call,
                        run_id=context.run_id,
                        step_id=step_id,
                        tool_name=tool_name,
                        arguments=validated,
                        idempotency_key=idempotency_key,
                    )
                    call_id = int(call_row["id"])
                    if cached:
                        result = call_row.get("result") or {}
                        return ToolExecutionResult(
                            content=str(result.get("content", "")),
                            tool_name=tool_name,
                            attempts=int(call_row.get("attempt_count") or attempt),
                            latency_ms=int(call_row.get("latency_ms") or 0),
                            cached=True,
                            artifact_path=call_row.get("result_artifact_path"),
                        )

        raise last_error or ToolExecutionError("TOOL_EXECUTION_FAILED", "Unknown tool error")

    @staticmethod
    async def _invoke(tool: Any, arguments: dict[str, Any]) -> Any:
        if hasattr(tool, "ainvoke"):
            return await tool.ainvoke(arguments)
        if hasattr(tool, "invoke"):
            return await asyncio.to_thread(tool.invoke, arguments)
        if asyncio.iscoroutinefunction(tool):
            return await tool(**arguments)
        return await asyncio.to_thread(tool, **arguments)

    @staticmethod
    def _idempotency_key(
        run_id: str, step_index: int, tool_name: str, arguments: dict
    ) -> str:
        body = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]
        return f"{run_id}:{step_index}:{tool_name}:{digest}"[:128]

    @staticmethod
    def _limit_result(
        run_id: str,
        idempotency_key: str,
        content: str,
        max_chars: int,
    ) -> tuple[str, str | None]:
        if len(content) <= max_chars:
            return content, None
        artifact_dir = Path("memory") / "artifacts" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        path = artifact_dir / f"tool_{digest}.txt"
        path.write_text(content, encoding="utf-8")
        preview = content[:max_chars]
        preview += f"\n\n[工具结果已截断，完整内容: {path.as_posix()}]"
        return preview, path.as_posix()


tool_gateway = ToolGateway()
