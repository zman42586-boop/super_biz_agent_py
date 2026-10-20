"""Register source material before model summarization or context compaction.

Only explicit adapters can create incident facts. A search query, model summary
or arbitrary tool string never becomes an observed fact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.models.diagnosis import Evidence
from app.services.document_identity import digest


def make_evidence(*, kind: str, source_type: str, source: str, locator: str,
                  text: str, context: dict | None = None, **metadata) -> dict:
    context = context or {}
    content_hash = digest(text)
    identity = json.dumps([kind, source_type, source, locator, content_hash,
                           context.get("run_id", ""), context.get("host_id", ""), metadata],
                          sort_keys=True, ensure_ascii=False)
    return Evidence(evidence_id="ev_" + digest(identity)[:24], kind=kind,
                    source_type=source_type, source=source, locator=locator, text=text,
                    content_hash=content_hash, run_id=context.get("run_id", ""),
                    host_id=context.get("host_id", ""), **metadata).model_dump(mode="json")


def register(registry: dict, evidence: list[dict]) -> dict:
    result = dict(registry)
    for row in evidence:
        item = Evidence.model_validate(row).model_dump(mode="json")
        previous = result.get(item["evidence_id"])
        if previous and previous != item:
            raise ValueError("An evidence ID cannot be rebound to different source material")
        result[item["evidence_id"]] = item
    return result


def seed_evidence(payload: dict, context: dict) -> dict:
    evidence = []
    alert = payload.get("alert") or {}
    data = alert.get("evidence") or {}
    if alert:
        # Detector labels and alert names are metadata, not confirmed root causes.
        observation = {key: alert.get(key) for key in ("host", "metric", "value", "threshold", "ts")}
        evidence.append(make_evidence(kind="incident_fact", source_type="alert_observation",
                         source=f"alert:{alert.get('alert_id', '')}", locator="/observation",
                         text=json.dumps(observation, ensure_ascii=False), context=context,
                         event_time=alert.get("ts"), time_basis="observed"))
    if data.get("crash_log"):
        extra = data.get("extra") or {}
        evidence.append(make_evidence(kind="incident_fact", source_type="crash_dump_excerpt",
                         source=extra.get("dump_source") or "alert_payload:crash_log",
                         locator="excerpt:lines:1", text=data["crash_log"], context=context,
                         event_time=extra.get("dump_modified_at") or alert.get("ts"),
                         document_version=extra.get("dump_sha256"),
                         time_basis="observed" if extra.get("dump_modified_at") else "unknown"))
    if data.get("system_snapshot"):
        evidence.append(make_evidence(kind="incident_fact", source_type="system_snapshot",
                         source="alert_payload:system_snapshot", locator="/evidence/system_snapshot",
                         text=json.dumps(data["system_snapshot"], ensure_ascii=False), context=context,
                         event_time=alert.get("ts"), time_basis="observed"))
    if (data.get("extra") or {}).get("stack_trace"):
        extra = data["extra"]
        evidence.append(make_evidence(kind="incident_fact", source_type="crash_stack_excerpt",
                         source=extra.get("dump_source") or "alert_payload:stack_trace",
                         locator="extracted_stack:lines:1", text=extra["stack_trace"], context=context,
                         event_time=extra.get("dump_modified_at"), document_version=extra.get("dump_sha256"),
                         time_basis="observed" if extra.get("dump_modified_at") else "unknown"))
    for kind in ("metrics", "logs"):
        for row in (payload.get("history") or {}).get(kind, []):
            item = row["payload"]
            evidence.append(make_evidence(kind="incident_fact",
                             source_type="runtime_log" if kind == "logs" else "historical_metric",
                             source=item.get("source") or f"host_evidence:{row['evidence_id']}",
                             locator=f"host_evidence:{row['evidence_id']};offset:{item.get('offset', 0)}",
                             text=item.get("text") or json.dumps(item, ensure_ascii=False),
                             context=context, event_time=item.get("ts"),
                             received_at=row.get("received_at"), time_basis="received"))
    return register({}, evidence)


def tool_evidence(tool_name: str, result: Any, context: dict) -> list[dict]:
    if tool_name == "retrieve_knowledge":
        artifact = getattr(result, "artifact", None)
        if artifact is None and isinstance(result, tuple) and len(result) == 2:
            artifact = result[1]
        evidence = []
        for doc in artifact or []:
            metadata = doc.metadata
            evidence.append(make_evidence(kind="knowledge", source_type=metadata.get("_source_type", "knowledge"),
                             source=metadata.get("_source_key") or metadata.get("_source") or metadata.get("_file_name", "unknown"),
                             locator=metadata.get("_parent_id") or metadata.get("_header_path") or "legacy_document_excerpt",
                             text=doc.page_content, context={"run_id": context.get("run_id", "")},
                             document_id=metadata.get("_document_id"), document_version=metadata.get("_document_version"),
                             parent_id=metadata.get("_parent_id"), chunk_id=metadata.get("_chunk_id")))
        return evidence
    # Other tools need their own reviewed adapter. In particular MCP "real"
    # snapshots lacking a host and sample time cannot support this incident.
    value = getattr(result, "content", result)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, dict):
        return []
    if value.get("status") == "error" or value.get("error"):
        return []
    raw = value.get("raw_result", value)
    if not isinstance(raw, dict):
        return []
    evidence = []
    if tool_name == "search_log":
        for row in raw.get("logs", []):
            evidence.append(make_evidence(kind="incident_fact", source_type="api_log",
                             source=row["source"], locator=f"line:{row['line_number']};log_id:{row['log_id']}",
                             text=row["message"], context={"run_id": context.get("run_id", ""),
                                                        "host_id": raw.get("host_id", "")},
                             event_time=row.get("timestamp"), time_basis="observed"))
    if tool_name in {"query_cpu_metrics", "query_memory_metrics", "get_lhm_temperature"}:
        if raw.get("host_id") and raw.get("observed_at") and raw.get("data_source") == "current_snapshot":
            evidence.append(make_evidence(kind="incident_fact", source_type="current_metric",
                             source=f"mcp:{tool_name}", locator="/current_snapshot",
                             text=json.dumps(raw, ensure_ascii=False),
                             context={"run_id": context.get("run_id", ""), "host_id": raw["host_id"]},
                             event_time=raw["observed_at"], time_basis="observed"))
    return evidence


def ordered_evidence(registry: dict) -> list[dict]:
    """Keep diagnostic logs ahead of potentially thousands of metric samples."""
    priority = {"crash_dump_excerpt": 0, "crash_stack_excerpt": 0, "runtime_log": 1,
                "system_snapshot": 2, "alert_observation": 2, "historical_metric": 3}

    def order(row):
        timestamp = utc_time(row.get("received_at")) or utc_time(row.get("event_time"))
        return (priority.get(row["source_type"], 4) if row["kind"] == "incident_fact" else 5,
                -timestamp.timestamp() if timestamp else 0)

    return sorted(registry.values(), key=order)


def evidence_context(registry: dict, *, max_chars: int = 24000) -> str:
    """Whole records only; show local line numbers used by Citation validation."""
    parts, used = [], 0
    # Incident facts precede knowledge; do not let long SOPs crowd out the dump.
    for row in ordered_evidence(registry):
        numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(row["text"].splitlines(), 1))
        text = json.dumps({key: value for key, value in row.items() if key != "text"}, ensure_ascii=False) + "\n" + numbered
        if used + len(text) > max_chars:
            continue
        parts.append(text)
        used += len(text)
    return "\n\n".join(parts) or "无已注册证据。必须报告原因未确定。"


def utc_time(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Unknown timezone cannot be used for a deterministic mismatch decision.
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None
