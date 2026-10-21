"""Bounded, host-scoped queries against a real Prometheus HTTP API.

Only reviewed expressions are exposed. Evaluation timestamps are labelled as
such; range-query values are not raw exporter samples or interpolated history.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

METRICS = {"cpu_percent", "memory_percent", "memory_total_bytes", "memory_available_bytes"}


def utc_datetime(value: str | None, default: datetime | None = None) -> datetime:
    if not value:
        if default is None:
            raise ValueError("A timestamp with timezone is required")
        return default
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Use ISO-8601 with timezone, for example 2026-10-21T08:00:00Z")
    return parsed.astimezone(UTC)


def expression(host_id: str, metric: str, freshness_seconds: int = 60) -> str:
    if not host_id.strip() or len(host_id) > 128 or any(ord(c) < 32 for c in host_id):
        raise ValueError("A valid explicit host_id is required")
    if metric not in METRICS:
        raise ValueError(f"Unsupported metric; choose from {sorted(METRICS)}")
    host = "host_id=" + json.dumps(host_id, ensure_ascii=False)
    windows = host + ',job="windows"'
    if metric == "cpu_percent":
        value = f'100 * (1 - avg by (host_id) (rate(windows_cpu_time_total{{{windows},mode="idle"}}[1m])))'
    elif metric == "memory_percent":
        available = f"max by (host_id) (windows_memory_available_bytes{{{windows}}})"
        total = f"max by (host_id) (windows_memory_physical_total_bytes{{{windows}}})"
        value = f"100 * (1 - ({available} / {total}))"
    else:
        name = "windows_memory_physical_total_bytes" if metric == "memory_total_bytes" else "windows_memory_available_bytes"
        value = f"max by (host_id) ({name}{{{windows}}})"
    job = "windows"
    healthy = f'max by (host_id) (up{{{host},job="{job}"}}) == 1'
    healthy += f' and on (host_id) (time() - max by (host_id) (timestamp(up{{{host},job="{job}"}})) <= {freshness_seconds})'
    healthy += f' and on (host_id) (count by (host_id) (up{{{host},job="{job}"}}) == 1)'
    return f"({value}) and on (host_id) ({healthy})"


class PrometheusClient:
    def __init__(self, url: str, *, token: str = "", timeout: float = 5.0,
                 max_range_seconds: int = 7200, max_points: int = 240,
                 freshness_seconds: int = 60, transport=None):
        self.url = url.rstrip("/")
        self.token, self.timeout = token, timeout
        self.max_range_seconds, self.max_points = max_range_seconds, max_points
        self.freshness_seconds, self.transport = freshness_seconds, transport

    @classmethod
    def from_settings(cls, settings):
        return cls(settings.prometheus_url, token=settings.prometheus_bearer_token,
                   timeout=settings.prometheus_timeout_sec,
                   max_range_seconds=settings.prometheus_max_range_sec,
                   max_points=settings.prometheus_max_points,
                   freshness_seconds=settings.prometheus_freshness_sec)

    def query(self, host_id: str, metric: str, *, start_time: str | None = None,
              end_time: str | None = None, step_seconds: int = 15,
              now: datetime | None = None) -> dict[str, Any]:
        promql = expression(host_id, metric, self.freshness_seconds)
        now = now or datetime.now(UTC)
        end = utc_datetime(end_time, now)
        if end > now + timedelta(seconds=5):
            raise ValueError("Future queries are not allowed")
        if start_time:
            start = utc_datetime(start_time)
            duration = (end - start).total_seconds()
            if duration <= 0 or duration > self.max_range_seconds:
                raise ValueError(f"Query range must be within 1..{self.max_range_seconds} seconds")
            if step_seconds < 5 or duration / step_seconds + 1 > self.max_points:
                raise ValueError(f"Use step >= 5s and at most {self.max_points} evaluation points")
            endpoint, expected = "/api/v1/query_range", "matrix"
            params = {"query": promql, "start": start.timestamp(), "end": end.timestamp(), "step": step_seconds}
        else:
            endpoint, expected = "/api/v1/query", "vector"
            params = {"query": promql, "time": end.timestamp()}
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                response = client.get(self.url + endpoint, params=params, headers=headers)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"Prometheus unavailable ({type(exc).__name__})") from None
        if body.get("status") != "success" or body.get("data", {}).get("resultType") != expected:
            raise RuntimeError("Prometheus returned an unsuccessful or unexpected result")
        results = body["data"].get("result", [])
        if len(results) > 1:
            raise RuntimeError("Ambiguous host series; fix duplicate host_id labels")
        points = []
        for series in results:
            if series.get("metric", {}).get("host_id") != host_id:
                raise RuntimeError("Prometheus returned a different or missing host_id")
            pairs = series.get("values", []) if start_time else [series["value"]]
            if len(pairs) > self.max_points:
                raise RuntimeError("Prometheus returned too many points")
            for timestamp, raw_value in pairs:
                value = float(raw_value)
                evaluated = datetime.fromtimestamp(float(timestamp), UTC)
                if not math.isfinite(value):
                    continue
                # Prometheus truncates API evaluation timestamps to milliseconds.
                # ISO-8601 callers can supply microseconds; tolerate only that
                # sub-millisecond representation difference at either boundary.
                if evaluated > end + timedelta(milliseconds=1) or (start_time and evaluated < start - timedelta(milliseconds=1)):
                    raise RuntimeError("Prometheus returned a point outside the requested range")
                if not start_time and (end - evaluated).total_seconds() > self.freshness_seconds:
                    continue
                points.append({"timestamp": evaluated.isoformat(), "value": value})
        return {"status": "success" if points else "unavailable", "host_id": host_id,
                "metric_name": metric, "data_source": "prometheus", "query": promql,
                "requested_range": {"start": start_time, "end": end.isoformat()},
                "timestamp_basis": "promql_evaluation", "historical_available": bool(start_time and points),
                "data_points": points, "warnings": body.get("warnings", []),
                "message": "真实 PromQL 计算结果；空缺不补零，时间点是查询求值时间，CPU 为一分钟 rate。"}
