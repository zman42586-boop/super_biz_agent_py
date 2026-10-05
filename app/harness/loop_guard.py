"""Deterministic safety limits for Agent loops and repeated actions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.harness.config import HarnessSettings, harness_settings
from app.harness.progress import evidence_keys
from app.tool_observation import DYNAMIC_TOOL_NAMES

_NON_PROGRESS_MARKERS = (
    "执行失败",
    "未找到",
    "没有找到",
    "无结果",
    "暂无数据",
    "tool_execution_failed",
    "tool_unavailable",
    '"ok": false',
)


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def tool_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    """Return a stable fingerprint independent of the plan step index."""
    body = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    raw = f"{tool_name.strip().lower()}:{body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoopGuardPolicy:
    max_steps: int = 8
    max_tool_calls: int = 20
    max_repeated_steps: int = 2
    max_repeated_tool_calls: int = 3
    max_no_progress_steps: int = 2
    min_dynamic_call_interval_seconds: float = 5.0

    @classmethod
    def from_settings(cls, settings: HarnessSettings) -> LoopGuardPolicy:
        return cls(
            max_steps=max(1, settings.max_steps),
            max_tool_calls=max(1, settings.max_tool_calls),
            max_repeated_steps=max(1, settings.max_repeated_steps),
            max_repeated_tool_calls=max(1, settings.max_repeated_tool_calls),
            max_no_progress_steps=max(1, settings.max_no_progress_steps),
            min_dynamic_call_interval_seconds=max(0.0, settings.min_dynamic_call_interval_seconds),
        )


@dataclass(frozen=True)
class LoopGuardDecision:
    allowed: bool
    reason: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def event_payload(self, scope: str) -> dict[str, Any]:
        return {
            "scope": scope,
            "reason": self.reason,
            "message": self.message,
            **self.details,
        }


class LoopGuard:
    def __init__(self, policy: LoopGuardPolicy | None = None) -> None:
        self.policy = policy or LoopGuardPolicy.from_settings(harness_settings)

    def evaluate_step(
        self,
        task: str,
        past_steps: list[tuple[Any, Any]],
    ) -> LoopGuardDecision:
        step_count = len(past_steps)
        if step_count >= self.policy.max_steps:
            return LoopGuardDecision(
                False,
                "max_steps",
                "已达到最大执行步骤数，基于现有证据生成报告。",
                {"step_count": step_count, "limit": self.policy.max_steps},
            )

        normalized_task = _normalize_text(task)
        matching_results = [
            result
            for previous_task, result in past_steps
            if _normalize_text(previous_task) == normalized_task
        ]
        # Only apply the old repeated-step shortcut to unstructured identical
        # text. Structured/dynamic observations are judged by evidence instead.
        repeated_text = (
            len(matching_results) >= self.policy.max_repeated_steps
            and all(evidence_keys(result) is None for result in matching_results)
            and len({_normalize_text(result) for result in matching_results}) == 1
        )
        if normalized_task and repeated_text:
            return LoopGuardDecision(
                False,
                "repeated_step",
                "相同步骤已重复执行，停止循环并基于现有证据生成报告。",
                {
                    "step": task,
                    "previous_occurrences": len(matching_results),
                    "limit": self.policy.max_repeated_steps,
                },
            )

        if self._has_no_progress(past_steps):
            return LoopGuardDecision(
                False,
                "no_progress",
                "连续步骤没有产生新证据，停止循环并生成证据不足报告。",
                {
                    "consecutive_steps": self.policy.max_no_progress_steps,
                    "limit": self.policy.max_no_progress_steps,
                },
            )

        return LoopGuardDecision(True)

    def evaluate_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        existing_calls: list[dict[str, Any]],
    ) -> LoopGuardDecision:
        if len(existing_calls) >= self.policy.max_tool_calls:
            return LoopGuardDecision(
                False,
                "max_tool_calls",
                "本次 Run 已达到最大工具调用数。",
                {"tool_call_count": len(existing_calls), "limit": self.policy.max_tool_calls},
            )

        current = tool_fingerprint(tool_name, arguments)
        repeated = sum(
            tool_fingerprint(str(call.get("tool_name", "")), call.get("arguments") or {}) == current
            for call in existing_calls
        )
        if tool_name in DYNAMIC_TOOL_NAMES:
            matching = [
                call
                for call in existing_calls
                if tool_fingerprint(str(call.get("tool_name", "")), call.get("arguments") or {})
                == current
            ]
            if matching and self.policy.min_dynamic_call_interval_seconds > 0:
                last_call = matching[-1]
                last_at = last_call.get("finished_at") or last_call.get("started_at")
                try:
                    timestamp = datetime.fromisoformat(str(last_at))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=UTC)
                    elapsed = (datetime.now(UTC) - timestamp).total_seconds()
                except (TypeError, ValueError):
                    elapsed = self.policy.min_dynamic_call_interval_seconds
                if elapsed < self.policy.min_dynamic_call_interval_seconds:
                    return LoopGuardDecision(
                        False,
                        "dynamic_tool_interval",
                        "实时工具刚查询过相同参数，请等待下一个采样间隔或调整查询条件。",
                        {
                            "tool_name": tool_name,
                            "min_interval_seconds": self.policy.min_dynamic_call_interval_seconds,
                            "fingerprint": current[:16],
                        },
                    )
            return LoopGuardDecision(True)

        if repeated >= self.policy.max_repeated_tool_calls:
            return LoopGuardDecision(
                False,
                "repeated_tool_call",
                "相同工具和参数已重复调用，请复用已有证据或调整查询条件。",
                {
                    "tool_name": tool_name,
                    "previous_occurrences": repeated,
                    "limit": self.policy.max_repeated_tool_calls,
                    "fingerprint": current[:16],
                },
            )

        return LoopGuardDecision(True)

    def _has_no_progress(self, past_steps: list[tuple[Any, Any]]) -> bool:
        limit = self.policy.max_no_progress_steps
        if len(past_steps) < limit:
            return False

        seen: set[str] = set()
        no_progress = 0
        previous_text = ""
        for _, result in past_steps:
            keys = evidence_keys(result)
            if keys is not None:
                progressed = bool(keys - seen)
                seen.update(keys)
            else:
                result_text = str(result or "").strip()
                normalized = _normalize_text(result_text)
                failed = not result_text or any(
                    marker in result_text.lower() for marker in _NON_PROGRESS_MARKERS
                )
                progressed = not failed and (not previous_text or normalized != previous_text)
                previous_text = normalized
            no_progress = 0 if progressed else no_progress + 1
        return no_progress >= limit


loop_guard = LoopGuard()
