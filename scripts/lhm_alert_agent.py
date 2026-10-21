"""Host event agent (legacy filename): liveness, MATLAB exits, logs and Dump.

CPU/memory are sampled by windows_exporter and stored by local Prometheus.
This agent never samples resource metrics directly and has no temperature code.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil
import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)
sys.path.insert(0, str(PROJECT_ROOT))

from app.harness.log_collector import IncrementalLogCollector
from app.monitoring.prometheus import PrometheusClient

FASTAPI_BASE_URL = os.getenv("FASTAPI_BASE_URL", "http://127.0.0.1:9900").rstrip("/")
ALERT_WEBHOOK_TOKEN = os.getenv("ALERT_WEBHOOK_TOKEN", "")
LOCAL_PROMETHEUS_URL = os.getenv("LOCAL_PROMETHEUS_URL", "http://127.0.0.1:9090")
HOST_NAME = socket.gethostname()
MONITOR_PROCESS = os.getenv("ONCALL_MONITOR_PROCESS", "").strip()
MONITOR_CRASH_DIR = os.getenv("ONCALL_MONITOR_CRASH_DIR", "").strip()
MONITOR_CRASH_PATTERN = os.getenv("ONCALL_MONITOR_CRASH_PATTERN", "").strip()
POLL_INTERVAL_SEC = max(0.1, float(os.getenv("POLL_INTERVAL_SEC", "5")))
HEARTBEAT_INTERVAL_SEC = max(0.1, float(os.getenv("HEARTBEAT_INTERVAL_SEC", "5")))
MATLAB_LOG_GLOBS = [p.strip() for p in os.getenv("MATLAB_LOG_GLOBS", "").split(";") if p.strip()]
MATLAB_LOG_INTERVAL_SEC = max(0.1, float(os.getenv("MATLAB_LOG_INTERVAL_SEC", "2")))
EMERGENCY_SNAPSHOT_DIR = PROJECT_ROOT / "memory/emergency"


def local_metric(metric: str) -> dict:
    return PrometheusClient(LOCAL_PROMETHEUS_URL, timeout=2).query(HOST_NAME, metric)


def find_process(name: str) -> dict | None:
    """Detect identity/liveness only; no CPU or memory sampling."""
    if not name:
        return None
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if (process.info["name"] or "").lower() == name.lower():
                return {"pid": process.info["pid"], "name": process.info["name"]}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _expand_crash_dir(path_template: str) -> str:
    return re.sub(r"%(\w+)%", lambda match: os.environ.get(match[1], match[0]), path_template)


def find_latest_crash_dump(crash_dir: str, pattern: str) -> str | None:
    if not crash_dir or not pattern:
        return None
    files = glob.glob(os.path.join(_expand_crash_dir(crash_dir), pattern))
    return max(files, key=os.path.getmtime) if files else None


def parse_crash_dump(filepath: str) -> dict:
    """Keep the original text excerpt, stack and provenance; never execute it."""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as source:
            raw = source.read()
        modified_at = datetime.fromtimestamp(os.path.getmtime(filepath), UTC).isoformat()
    except OSError:
        return {"crash_type": "read_error", "stack_trace": "", "raw_snippet": "无法读取 Dump"}
    crash_type = "unknown"
    for keyword, kind in (("stack overflow", "stack_overflow"), ("access violation", "access_violation"),
                          ("segmentation violation", "segmentation_violation"), ("assertion failed", "assertion_failed"),
                          ("unexpected exception", "unexpected_exception"), ("out of memory", "out_of_memory")):
        if keyword in raw[:2000].lower():
            crash_type = kind
            break
    stack, collecting = [], False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if "STACK TRACE" in line.upper() or "CALL STACK" in line.upper():
            collecting = True
            continue
        if collecting:
            if not line and stack:
                break
            if not line or line.startswith(("Register", "R0:", "RAX:")):
                continue
            stack.append(line)
            if len(stack) >= 30:
                break
    return {"dump_source": os.path.basename(filepath), "dump_modified_at": modified_at,
            "dump_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "crash_type": crash_type,
            "stack_trace": "\n".join(stack) or "(未找到调用栈)", "raw_snippet": raw[:3000]}


def collect_system_snapshot() -> dict:
    snapshot = {"timestamp": datetime.now(UTC).isoformat(), "host": HOST_NAME,
                "data_source": "prometheus", "metric_points": {}}
    for metric in ("cpu_percent", "memory_percent"):
        try:
            row = local_metric(metric)
            if row["data_points"]:
                point = row["data_points"][-1]
                snapshot[metric] = point["value"]
                snapshot["metric_points"][metric] = point
            else:
                snapshot.setdefault("unavailable_metrics", []).append(metric)
        except (ValueError, RuntimeError):
            snapshot.setdefault("unavailable_metrics", []).append(metric)
    return snapshot


def write_light_snapshot(snapshot: dict, filename: str = "last_snapshot.json") -> None:
    try:
        EMERGENCY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        (EMERGENCY_SNAPSHOT_DIR / filename).write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def send_heartbeat() -> bool:
    try:
        response = requests.post(f"{FASTAPI_BASE_URL}/api/heartbeat", json={"host": HOST_NAME},
                                 headers={"Authorization": f"Bearer {ALERT_WEBHOOK_TOKEN}"}, timeout=5)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[WARN] Heartbeat retry pending: {type(exc).__name__}", flush=True)
        return False


def send_process_exit_alert(process_name: str, crash_info: dict, snapshot: dict) -> bool:
    stack = crash_info.get("stack_trace", "")
    alert_name = f"{process_name} 异常退出"
    if stack and stack != "(未找到调用栈)":
        alert_name += ": " + stack.splitlines()[0].strip()
    payload = {"source": "process_monitor", "host": HOST_NAME, "severity": "critical", "alert_name": alert_name,
               "metric": "process_exit", "value": 0, "threshold": 1, "duration_sec": 0,
               "sensor_id": process_name, "ts": datetime.now(UTC).isoformat(), "evidence": {
                   "crash_log": crash_info.get("raw_snippet", ""), "crash_type": crash_info.get("crash_type", "unknown"),
                   "monitored_process": process_name, "system_snapshot": snapshot,
                   "extra": {key: crash_info[key] for key in ("dump_source", "dump_modified_at", "dump_sha256", "stack_trace") if crash_info.get(key)}}}
    try:
        response = requests.post(f"{FASTAPI_BASE_URL}/api/alerts/ingest", json=payload,
                                 headers={"Authorization": f"Bearer {ALERT_WEBHOOK_TOKEN}"}, timeout=10)
        response.raise_for_status()
        print("[OK] Process exit alert accepted", flush=True)
        return True
    except requests.RequestException as exc:
        print(f"[WARN] Process exit alert delivery failed: {type(exc).__name__}", flush=True)
        return False


def heartbeat_loop(stopping):
    while not stopping.is_set():
        tick = time.monotonic()
        send_heartbeat()
        stopping.wait(max(0, HEARTBEAT_INTERVAL_SEC - (time.monotonic() - tick)))


def log_upload_loop(stopping):
    collector = IncrementalLogCollector(MATLAB_LOG_GLOBS, EMERGENCY_SNAPSHOT_DIR / "matlab_log_cursor.json",
                max_files=int(os.getenv("MATLAB_LOG_MAX_FILES", "8")), read_bytes=int(os.getenv("MATLAB_LOG_READ_BYTES", "65536")),
                log_chars=int(os.getenv("ONCALL_HISTORY_LOG_CHARS", "4096")), spool_entries=int(os.getenv("MATLAB_LOG_SPOOL_ENTRIES", "128")),
                spool_bytes=int(os.getenv("MATLAB_LOG_SPOOL_BYTES", "262144")))
    retry = MATLAB_LOG_INTERVAL_SEC
    while not stopping.is_set():
        try:
            collector.collect()
            batch = collector.batch(max_bytes=int(os.getenv("ONCALL_HISTORY_PAYLOAD_BYTES", "65536")))
            if batch:
                response = requests.post(f"{FASTAPI_BASE_URL}/api/host-logs", json={"host": HOST_NAME, "entries": batch},
                                         headers={"Authorization": f"Bearer {ALERT_WEBHOOK_TOKEN}"}, timeout=5)
                response.raise_for_status()
                collector.acknowledge(batch)
            retry = MATLAB_LOG_INTERVAL_SEC
        except Exception as exc:
            print(f"[WARN] Log upload retry pending: {type(exc).__name__}", flush=True)
            retry = min(60, max(MATLAB_LOG_INTERVAL_SEC, retry * 2))
        stopping.wait(retry)


def main():
    print(f"Host event agent: host={HOST_NAME}; Prometheus={LOCAL_PROMETHEUS_URL}", flush=True)
    last_dump_mtime, previous_pid = time.time(), None
    stopping = threading.Event()
    threads = [threading.Thread(target=heartbeat_loop, args=(stopping,), daemon=True)]
    if MATLAB_LOG_GLOBS:
        threads.append(threading.Thread(target=log_upload_loop, args=(stopping,), daemon=True))
    for thread in threads:
        thread.start()
    try:
        while not stopping.is_set():
            process = find_process(MONITOR_PROCESS)
            if previous_pid and (not process or process["pid"] != previous_pid):
                crash_info = {"crash_type": "unknown", "stack_trace": "", "raw_snippet": ""}
                dump = find_latest_crash_dump(MONITOR_CRASH_DIR, MONITOR_CRASH_PATTERN)
                if dump and os.path.getmtime(dump) > last_dump_mtime:
                    last_dump_mtime = os.path.getmtime(dump)
                    crash_info = parse_crash_dump(dump)
                snapshot = collect_system_snapshot()
                send_process_exit_alert(MONITOR_PROCESS, crash_info, snapshot)
                write_light_snapshot({**snapshot, "process_name": MONITOR_PROCESS, **crash_info}, "last_breath.json")
            previous_pid = process["pid"] if process else None
            stopping.wait(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        for thread in threads:
            thread.join(timeout=6)


if __name__ == "__main__":
    main()
