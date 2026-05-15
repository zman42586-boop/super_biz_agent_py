"""
LibreHardwareMonitor (LHM) 告警 Agent

职责：
  1. 每 POLL_INTERVAL_SEC 秒轮询 LHM Web API 获取温度传感器值
  2. 连续超过 ONCALL_TEMP_THRESHOLD_C 达 ONCALL_TEMP_DURATION_SEC 秒后触发告警
  3. 向 FastAPI 发送 POST /api/alerts/ingest webhook
  4. 冷却期 ONCALL_TEMP_COOLDOWN_SEC 秒内不重复触发

依赖（仅标准库 + requests + python-dotenv）：
  pip install requests python-dotenv

LibreHardwareMonitor 设置：
  1. 下载 LibreHardwareMonitor（https://github.com/LibreHardwareMonitor/LibreHardwareMonitor）
  2. 运行后进入 Options -> Remote Web Server -> 勾选 Run -> 端口默认 8085 -> Apply
  3. 浏览器访问 http://127.0.0.1:8085/data.json 确认数据

启动方式（在项目根目录）：
  .venv/Scripts/python.exe scripts/lhm_alert_agent.py
"""

from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    raise SystemExit("缺少 requests 库，请运行: pip install requests")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

# ── 配置（从环境变量读取，未设置则使用默认值）──────────────────────────────
LHM_BASE_URL = os.getenv("LHM_BASE_URL", "http://127.0.0.1:8085")
FASTAPI_BASE_URL = os.getenv("FASTAPI_BASE_URL", "http://127.0.0.1:9900")
ALERT_WEBHOOK_TOKEN = os.getenv("ALERT_WEBHOOK_TOKEN", "")
ONCALL_TEMP_THRESHOLD_C = float(os.getenv("ONCALL_TEMP_THRESHOLD_C", "50"))
ONCALL_TEMP_DURATION_SEC = int(os.getenv("ONCALL_TEMP_DURATION_SEC", "60"))
ONCALL_TEMP_COOLDOWN_SEC = int(os.getenv("ONCALL_TEMP_COOLDOWN_SEC", "120"))
# 用于匹配目标温度传感器，包含此字符串即选中（不区分大小写）；AMD 常见为 CCDs Max (Tdie)
LHM_SENSOR_NAME_CONTAINS = os.getenv("LHM_SENSOR_NAME_CONTAINS", "CCDs Max (Tdie)")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "5"))


# ── LHM 数据解析 ──────────────────────────────────────────────────────────

def _find_sensors(node: dict, name_contains: str, results: list) -> None:
    """递归遍历 LHM data.json 节点，收集匹配的温度传感器。"""
    children = node.get("Children", [])
    sensor_type = node.get("Type", "")
    name = node.get("Text", "")

    if sensor_type == "Temperature" and name_contains.lower() in name.lower():
        try:
            raw_value = node.get("Value", "0")
            # LHM 返回格式例如 "45.2 °C"
            value = float(str(raw_value).split()[0].replace(",", "."))
            results.append({
                "id": node.get("id", ""),
                "name": name,
                "value": value,
            })
        except (ValueError, IndexError):
            pass

    for child in children:
        _find_sensors(child, name_contains, results)


def fetch_temperature(name_contains: str) -> Optional[dict]:
    """从 LHM Web API 拉取目标温度传感器当前值。

    Returns:
        {"id": ..., "name": ..., "value": float} 或 None（找不到/网络错误）
    """
    url = f"{LHM_BASE_URL}/data.json"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"[WARN] LHM 请求失败: {exc}")
        return None

    results: list = []
    _find_sensors(data, name_contains, results)

    if not results:
        return None
    # 优先选第一个匹配
    return results[0]


# ── 心跳上报 ────────────────────────────────────────────────────────────

HOST_NAME = socket.gethostname()
EMERGENCY_SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "memory" / "emergency"

_last_heartbeat_ok = True
_last_snapshot_temp = 0.0  # 避免短时间内重复写快照

# 温度达到此值触发紧急快照（死前留证）
EMERGENCY_SNAPSHOT_TEMP_C = float(os.getenv("EMERGENCY_SNAPSHOT_TEMP_C", "85"))


def _collect_critical_snapshot(temp: float, recent_points: list) -> dict:
    """在温度危险时采集系统快照——死前最后一口气留点证据。
    即使 psutil 不可用（边缘 Agent 是可选的），也至少返回温度数据。
    """
    snapshot: dict = {
        "temperature": temp,
        "recent_points": recent_points[-10:],
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    try:
        import psutil
        snapshot["cpu_percent"] = round(psutil.cpu_percent(interval=0.5), 1)
        vm = psutil.virtual_memory()
        snapshot["memory_percent"] = round(vm.percent, 1)
        top_procs = []
        for proc in sorted(
            psutil.process_iter(["name", "cpu_percent"]),
            key=lambda p: p.info["cpu_percent"] or 0,
            reverse=True,
        )[:5]:
            try:
                top_procs.append({
                    "name": proc.info["name"],
                    "cpu": proc.info["cpu_percent"] or 0,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        snapshot["top_processes"] = top_procs
    except ImportError:
        pass  # 边缘 Agent 不强依赖 psutil
    return snapshot


def _write_emergency_snapshot(snapshot: dict) -> None:
    """死前快照落盘——就算机子挂了，重启回来还能看到。"""
    global _last_snapshot_temp
    temp = snapshot.get("temperature", 0)
    # 温度变化小于 2°C 不重复写，减少磁盘 IO
    if abs(temp - _last_snapshot_temp) < 2.0:
        return
    _last_snapshot_temp = temp
    try:
        EMERGENCY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = EMERGENCY_SNAPSHOT_DIR / "last_breath.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        print(f"\n[EMERGENCY] 紧急快照已落盘: {path} (温度 {temp}°C)")
    except OSError as exc:
        print(f"\n[WARN] 紧急快照写入失败: {exc}")


def send_heartbeat(temp: float | None, recent_points: list) -> bool:
    """每个轮询周期向云端上报心跳 + 当前系统状态。

    temp=None 表示 LHM 不可达。云端通过心跳中断判定主机失联，
    并通过最后一条心跳的系统状态推断死因。
    """
    global _last_heartbeat_ok
    url = f"{FASTAPI_BASE_URL}/api/heartbeat"
    headers = {"Authorization": f"Bearer {ALERT_WEBHOOK_TOKEN}"}
    payload: dict = {"host": HOST_NAME}

    if temp is not None:
        payload["temperature"] = round(temp, 1)
        payload["temp_threshold"] = ONCALL_TEMP_THRESHOLD_C
        payload["recent_points"] = recent_points[-10:]

    # 温度危险时采集完整快照（死前遗书）
    if temp is not None and temp >= EMERGENCY_SNAPSHOT_TEMP_C:
        snapshot = _collect_critical_snapshot(temp, recent_points)
        payload["critical_snapshot"] = snapshot
        _write_emergency_snapshot(snapshot)

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
        if not _last_heartbeat_ok:
            print(f"[OK] 心跳恢复")
        _last_heartbeat_ok = True
        return True
    except requests.RequestException as exc:
        if _last_heartbeat_ok:
            print(f"[WARN] 心跳发送失败: {exc}")
        _last_heartbeat_ok = False
        return False


# ── Webhook 上报 ──────────────────────────────────────────────────────────

def send_webhook(sensor: dict, value: float, recent_points: list) -> bool:
    """向 FastAPI /api/alerts/ingest 发送告警事件。"""
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "source": "librehardwaremonitor",
        "host": HOST_NAME,
        "severity": "critical" if value >= ONCALL_TEMP_THRESHOLD_C + 5 else "warning",
        "alert_name": f"{sensor['name']} High",
        "metric": "temperature_c",
        "value": value,
        "threshold": ONCALL_TEMP_THRESHOLD_C,
        "duration_sec": ONCALL_TEMP_DURATION_SEC,
        "sensor_id": str(sensor.get("id", "")),
        "ts": ts,
        "evidence": {
            "lhm_url": f"{LHM_BASE_URL}/data.json",
            "recent_points": recent_points[-20:],
        },
    }
    url = f"{FASTAPI_BASE_URL}/api/alerts/ingest"
    headers = {"Authorization": f"Bearer {ALERT_WEBHOOK_TOKEN}"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        print(f"[OK] Webhook 发送成功: {resp.json()}")
        return True
    except requests.RequestException as exc:
        print(f"[ERROR] Webhook 发送失败: {exc}")
        return False


# ── 主循环 ────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"LHM Alert Agent 启动")
    print(f"  LHM URL:      {LHM_BASE_URL}")
    print(f"  FastAPI URL:  {FASTAPI_BASE_URL}")
    print(f"  传感器匹配:   包含 '{LHM_SENSOR_NAME_CONTAINS}'")
    print(f"  告警阈值:     {ONCALL_TEMP_THRESHOLD_C}°C 连续 {ONCALL_TEMP_DURATION_SEC}s")
    print(f"  冷却期:       {ONCALL_TEMP_COOLDOWN_SEC}s")
    print(f"  轮询间隔:     {POLL_INTERVAL_SEC}s")
    print()

    breach_start: Optional[float] = None   # 本次持续超阈开始时间
    last_alert_at: Optional[float] = None  # 上次发出告警的时间
    recent_points: list = []               # 最近数据点 [(时间字符串, 值), ...]

    while True:
        # 1. 读取温度传感器
        sensor = fetch_temperature(LHM_SENSOR_NAME_CONTAINS)

        # 2. 发送心跳（带上当前温度，供云端死因分析）
        current_temp = sensor["value"] if sensor else None
        send_heartbeat(current_temp, recent_points)

        if sensor is None:
            print(f"[WARN] 未找到传感器 '{LHM_SENSOR_NAME_CONTAINS}'，LHM 是否已启动？")
            breach_start = None
            time.sleep(POLL_INTERVAL_SEC)
            continue

        value = sensor["value"]
        ts_str = datetime.now().strftime("%H:%M:%S")
        recent_points.append([ts_str, value])
        if len(recent_points) > 60:
            recent_points = recent_points[-60:]

        print(f"[{ts_str}] {sensor['name']}: {value:.1f}°C", end="")

        now = time.monotonic()

        if value >= ONCALL_TEMP_THRESHOLD_C:
            if breach_start is None:
                breach_start = now
                print(f"  [超阈开始]", end="")
            elapsed = now - breach_start
            print(f"  [持续 {elapsed:.0f}s / 需 {ONCALL_TEMP_DURATION_SEC}s]", end="")

            # 判断是否满足持续时长且不在冷却期内
            if elapsed >= ONCALL_TEMP_DURATION_SEC:
                in_cooldown = (
                    last_alert_at is not None
                    and (now - last_alert_at) < ONCALL_TEMP_COOLDOWN_SEC
                )
                if not in_cooldown:
                    print()
                    print(f"[ALERT] 温度 {value:.1f}°C 持续 {elapsed:.0f}s，触发告警！")
                    ok = send_webhook(sensor, value, recent_points)
                    if ok:
                        last_alert_at = now
                        breach_start = now  # 重置计时，等下一轮冷却后再触发
                else:
                    remaining = ONCALL_TEMP_COOLDOWN_SEC - (now - last_alert_at)
                    print(f"  [冷却中 剩余 {remaining:.0f}s]", end="")
        else:
            if breach_start is not None:
                print(f"  [已恢复正常]", end="")
            breach_start = None

        print()
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
