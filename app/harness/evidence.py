"""Size limits and best-effort redaction shared by ingest and log collection."""

import json
import re

SECRET = re.compile(
    r"""(?i)((?:api[_-]?key|password|passwd|smtp[_-]?pass|token|secret|authorization|cookie)["']?\s*[=:]\s*["']?)([^\s,;"']+)"""
)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
USER_PATH = re.compile(r"(?i)([A-Z]:\\Users\\)[^\\\s]+|(/home/)[^/\s]+")


def redact(text: str, limit: int = 4096) -> str:
    text = BEARER.sub("Bearer [REDACTED]", text)
    text = SECRET.sub(r"\1[REDACTED]", text)
    text = KEY.sub("[REDACTED]", text)
    text = EMAIL.sub("[EMAIL]", text)
    text = USER_PATH.sub(lambda m: (m.group(1) or m.group(2)) + "[USER]", text)
    return text[:limit]


def safe_snapshot(snapshot: dict) -> dict:
    # Only reviewed light metric fields, never arbitrary client JSON/logs.
    result = {
        key: value
        for key, value in snapshot.items()
        if key in {"cpu_percent", "memory_percent", "temperature", "temp_threshold"}
        and (value is None or isinstance(value, (int, float)))
    }
    result["top_processes"] = [
        {
            "name": redact(str(p.get("name", "")), 128),
            "cpu": p.get("cpu") if isinstance(p.get("cpu"), (int, float)) else None,
            "memory": p.get("memory") if isinstance(p.get("memory"), (int, float)) else None,
        }
        for p in snapshot.get("top_processes", [])[:5]
        if isinstance(p, dict)
    ]
    return json.loads(json.dumps(result, default=str))
