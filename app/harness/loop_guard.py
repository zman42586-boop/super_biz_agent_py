"""Deterministic safety limits for Agent loops and repeated actions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.harness.config import HarnessSettings, harness_settings

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
    max_repeated_tool_calls: int = 2
    max_no_progress_steps: int = 2

    @classmethod
    def from_settings(cls, settings: HarnessSettings) -> LoopGuardPolicy:
        return cls(
            max_steps=max(1, settings.max_steps),
            max_tool_calls=max(1, settings.max_tool_calls),
            max_repeated_steps=max(1, settings.max_repeated_steps),
            max_repeated_tool_calls=max(1, settings.max_repeated_tool_calls),
            max_no_progress_steps=max(1, settings.max_no_progress_steps),
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
        repeated = sum(
            _normalize_text(previous_task) == normalized_task
            for previous_task, _ in past_steps
        )
        if normalized_task and repeated >= self.policy.max_repeated_steps:
            return LoopGuardDecision(
                False,
                "repeated_step",
                "相同步骤已重复执行，停止循环并基于现有证据生成报告。",
                {
                    "step": task,
                    "previous_occurrences": repeated,
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
            tool_fingerprint(str(call.get("tool_name", "")), call.get("arguments") or {})
            == current
            for call in existing_calls
        )
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

        recent_results = [str(result or "").strip() for _, result in past_steps[-limit:]]
        normalized = [_normalize_text(result) for result in recent_results]
        if all(
            not result
            or any(marker in result.lower() for marker in _NON_PROGRESS_MARKERS)
            for result in recent_results
        ):
            return True
        return bool(normalized[0]) and len(set(normalized)) == 1


loop_guard = LoopGuard()
