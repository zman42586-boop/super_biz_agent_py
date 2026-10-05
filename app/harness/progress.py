"""Small, deterministic progress signals from persisted ToolResult facts.

These signals describe *new observations*, not a diagnosis. The replanner still
decides whether an observation explains the incident. Unknown tool formats fall
back to the legacy step-result check instead of guessing from timestamps/text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def evidence_keys(step_result: Any) -> set[str] | None:
    """Return stable evidence keys, or None when the format is not understood."""
    if not isinstance(step_result, Mapping) or step_result.get("schema") != "tool_result.v1":
        return None
    step_facts = step_result.get("key_facts")
    if not isinstance(step_facts, Mapping):
        return None
    tool_facts = step_facts.get("tools")
    if not isinstance(tool_facts, list):
        return None

    keys: set[str] = set()
    understood = False
    for tool in tool_facts:
        if not isinstance(tool, Mapping):
            continue
        name = str(tool.get("tool_name", ""))
        facts = tool.get("key_facts")
        if not isinstance(facts, Mapping):
            continue
        if str(tool.get("status", "success")) != "success":
            understood = True
            continue

        identity = _identity(name, facts)
        if name == "search_log":
            understood = True
            for log_id in facts.get("log_ids", []):
                keys.add(_key(name, identity, "log", str(log_id)))
        elif name in {"query_cpu_metrics", "query_memory_metrics", "get_lhm_temperature"}:
            understood = True
            state = _metric_state(name, facts)
            if state is not None:
                keys.add(_key(name, identity, "state", state))
        elif name == "list_lhm_sensors":
            understood = True
            if isinstance(facts.get("total"), int):
                keys.add(_key(name, identity, "sensor_count", facts["total"]))
        elif name == "get_current_time":
            # A newer clock reading is not new diagnostic evidence.
            understood = True
        else:
            ids = facts.get("evidence_ids")
            if isinstance(ids, list):
                understood = True
                for evidence_id in ids:
                    keys.add(_key(name, identity, "evidence", str(evidence_id)))
            elif facts.get("error_code"):
                understood = True
                keys.add(_key(name, identity, "error", str(facts["error_code"])))
    return keys if understood else None


def _key(*parts: Any) -> str:
    return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)


def _identity(name: str, facts: Mapping[str, Any]) -> str:
    if name == "search_log":
        # The same line found by two different queries is still one observation.
        return "log"
    return str(
        facts.get("service_name")
        or facts.get("sensor_id")
        or facts.get("sensor_name")
        or facts.get("lhm_base_url")
        or "default"
    )


def _metric_state(name: str, facts: Mapping[str, Any]) -> str | None:
    value_field = {
        "query_cpu_metrics": "current_cpu_percent",
        "query_memory_metrics": "current_memory_percent",
        "get_lhm_temperature": "value_c",
    }[name]
    value = facts.get(value_field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    alert = facts.get("alert_info")
    if isinstance(alert, Mapping) and isinstance(alert.get("triggered"), bool):
        return "abnormal" if alert["triggered"] else "normal"
    threshold = alert.get("threshold") if isinstance(alert, Mapping) else None
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        threshold = {
            "query_cpu_metrics": 80.0,
            "query_memory_metrics": 70.0,
            "get_lhm_temperature": 50.0,
        }[name]
    return "abnormal" if value >= threshold else "normal"
