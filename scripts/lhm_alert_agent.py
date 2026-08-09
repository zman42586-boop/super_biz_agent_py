"""
SuperBizAgent — 本机 OnCall 检测 Agent

职责：
  1. 每 POLL_INTERVAL_SEC 秒轮询 LibreHardwareMonitor Web API 获取温度
  2. 连续超过 ONCALL_TEMP_THRESHOLD_C 达 ONCALL_TEMP_DURATION_SEC 秒后触发告警
  3. 向 FastAPI 发送 POST /api/alerts/ingest webhook
  4. 冷却期 ONCALL_TEMP_COOLDOWN_SEC 秒内不重复触发

进程监控（v2）：
  5. 监听指定进程是否存活；进程消失 → 扫描崩溃日志 → 立刻告警
  6. 每轮心跳携带完整系统快照（CPU/内存/TOP 进程），不再只在高温时采集
  7. 持续写 light_snapshot.json 到磁盘（供重启后死因分析）
  8. 启动自检：检测上次是否异常退出，若是则补充发送死前报告

依赖（仅标准库 + requests + python-dotenv + psutil）：
  pip install requests python-dotenv psutil

LibreHardwareMonitor 设置：
  1. 下载 LibreHardwareMonitor（https://github.com/LibreHardwareMonitor/LibreHardwareMonitor）
  2. 运行后进入 Options -> Remote Web Server -> 勾选 Run -> 端口默认 8085 -> Apply
  3. 浏览器访问 http://127.0.0.1:8085/data.json 确认数据

启动方式（在项目根目录）：
  .venv/Scripts/python.exe scripts/lhm_alert_agent.py
"""

from __future__ import annotations

import glob as glob_mod
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

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ── 配置（从环境变量读取，未设置则使用默认值）──────────────────────────────
LHM_BASE_URL = os.getenv("LHM_BASE_URL", "http://127.0.0.1:8085")
FASTAPI_BASE_URL = os.getenv("FASTAPI_BASE_URL", "http://127.0.0.1:9900")
ALERT_WEBHOOK_TOKEN = os.getenv("ALERT_WEBHOOK_TOKEN", "")
ONCALL_TEMP_ENABLED = os.getenv("ONCALL_TEMP_ENABLED", "false").lower() in ("true", "1", "yes")
ONCALL_TEMP_THRESHOLD_C = float(os.getenv("ONCALL_TEMP_THRESHOLD_C", "85"))
ONCALL_TEMP_DURATION_SEC = int(os.getenv("ONCALL_TEMP_DURATION_SEC", "60"))
ONCALL_TEMP_COOLDOWN_SEC = int(os.getenv("ONCALL_TEMP_COOLDOWN_SEC", "120"))
LHM_SENSOR_NAME_CONTAINS = os.getenv("LHM_SENSOR_NAME_CONTAINS", "CCDs Max (Tdie)")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "5"))

# 进程监控配置
MONITOR_PROCESS = os.getenv("ONCALL_MONITOR_PROCESS", "").strip()
MONITOR_CRASH_DIR = os.getenv("ONCALL_MONITOR_CRASH_DIR", "").strip()
MONITOR_CRASH_PATTERN = os.getenv("ONCALL_MONITOR_CRASH_PATTERN", "").strip()

HOST_NAME = socket.gethostname()
EMERGENCY_SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "memory" / "emergency"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 心跳状态
_last_heartbeat_ok = True

# 温度达到此值触发紧急快照（死前留证）
EMERGENCY_SNAPSHOT_TEMP_C = float(os.getenv("EMERGENCY_SNAPSHOT_TEMP_C", "85"))


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        LHM 温度数据解析                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _find_sensors(node: dict, name_contains: str, results: list) -> None:
    """递归遍历 LHM data.json 节点，收集匹配的温度传感器。"""
    children = node.get("Children", [])
    sensor_type = node.get("Type", "")
    name = node.get("Text", "")

    if sensor_type == "Temperature" and name_contains.lower() in name.lower():
        try:
            raw_value = node.get("Value", "0")
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
    """从 LHM Web API 拉取目标温度传感器当前值。"""
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
    return results[0]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        进程监控模块                                         ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def find_process(name: str) -> dict | None:
    """用进程名查找进程，返回 {pid, name, cpu_percent, memory_percent} 或 None。"""
    if not name or not HAS_PSUTIL:
        return None
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == name.lower():
                return {
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "cpu_percent": proc.info["cpu_percent"] or 0,
                    "memory_percent": round(proc.info["memory_percent"] or 0, 1) if proc.info["memory_percent"] else 0,
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _expand_crash_dir(path_template: str) -> str:
    """展开环境变量 %VAR% 到实际路径（Windows 兼容）。"""
    import re
    result = path_template
    for match in re.finditer(r"%(\w+)%", path_template):
        var_name = match.group(1)
        result = result.replace(f"%{var_name}%", os.environ.get(var_name, match.group(0)))
    return result


def find_latest_crash_dump(crash_dir: str, pattern: str) -> str | None:
    """在崩溃日志目录中找最新匹配的文件。"""
    if not crash_dir or not pattern:
        return None
    expanded = _expand_crash_dir(crash_dir)
    if not os.path.isdir(expanded):
        return None
    search_pattern = os.path.join(expanded, pattern)
    files = glob_mod.glob(search_pattern)
    if not files:
        return None
    # 返回修改时间最新的文件
    return max(files, key=os.path.getmtime)


def parse_crash_dump(filepath: str) -> dict:
    """解析 MATLAB 崩溃日志，提取关键信息。

    MATLAB crash dump 典型内容：
      - 'Segmentation violation detected' 或 'Stack Overflow'
      - 调用栈: matlab.exe+0x... 或 my_function at line 247
      - 寄存器状态 / 内存映射

    Returns:
        {crash_type, stack_trace, raw_snippet}
    """
    crash_type = "unknown"
    stack_lines: list[str] = []
    raw_header = ""

    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return {"crash_type": "read_error", "stack_trace": "", "raw_snippet": f"无法读取: {filepath}"}

    raw_header = raw[:3000] if len(raw) > 3000 else raw

    # 检测崩溃类型
    upper = raw[:2000].upper()
    keywords_map = [
        ("stack overflow", "stack_overflow"),
        ("access violation", "access_violation"),
        ("segmentation violation", "segmentation_violation"),
        ("assertion failed", "assertion_failed"),
        ("unexpected exception", "unexpected_exception"),
        ("out of memory", "out_of_memory"),
    ]
    for keyword, ctype in keywords_map:
        if keyword in upper:
            crash_type = ctype
            break

    # 提取调用栈行
    stack_section_started = False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        # 定位调用栈开始标记
        upper_line = line.upper()
        if "STACK TRACE" in upper_line or "CALL STACK" in upper_line:
            stack_section_started = True
            continue
        if stack_section_started:
            if not line:
                if stack_lines:
                    break  # 遇到空行且已有栈内容，结束
                continue
            # 跳过寄存器描述行
            if line.startswith("Register") or line.startswith("R0:") or line.startswith("RAX:"):
                continue
            stack_lines.append(line)
            if len(stack_lines) >= 30:  # 最多取30行
                break

    return {
        "crash_type": crash_type,
        "stack_trace": "\n".join(stack_lines) if stack_lines else "(未找到调用栈)",
        "raw_snippet": raw_header,
    }


def collect_system_snapshot() -> dict:
    """采集当前系统快照（始终可用，不依赖温度阈值）。"""
    snapshot: dict = {
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "host": HOST_NAME,
    }
    if HAS_PSUTIL:
        try:
            snapshot["cpu_percent"] = round(psutil.cpu_percent(interval=0.5), 1)
            vm = psutil.virtual_memory()
            snapshot["memory_percent"] = round(vm.percent, 1)
            snapshot["memory_used_gb"] = round(vm.used / (1024 ** 3), 2)
            snapshot["memory_total_gb"] = round(vm.total / (1024 ** 3), 2)
            top_procs = []
            for proc in sorted(
                psutil.process_iter(["name", "cpu_percent", "memory_percent"]),
                key=lambda p: p.info["cpu_percent"] or 0,
                reverse=True,
            )[:10]:
                try:
                    top_procs.append({
                        "name": proc.info["name"],
                        "cpu": round(proc.info["cpu_percent"] or 0, 1),
                        "memory": round(proc.info["memory_percent"] or 0, 1),
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            snapshot["top_processes"] = top_procs
        except Exception as e:
            snapshot["psutil_error"] = str(e)
    return snapshot


def write_light_snapshot(snapshot: dict) -> None:
    """每轮循环写一份轻量快照到磁盘（供重启后查阅最近状态）。"""
    try:
        EMERGENCY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = EMERGENCY_SNAPSHOT_DIR / "last_snapshot.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        心跳上报                                            ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def send_heartbeat(temp: float | None, recent_points: list,
                   cpu_pct: float | None = None,
                   mem_pct: float | None = None,
                   top_procs: list | None = None) -> bool:
    """每个轮询周期向主服务上报心跳 + 完整系统状态。

    temp=None 表示 LHM 不可达。服务端通过心跳中断判定主机失联，
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

    if cpu_pct is not None:
        payload["cpu_percent"] = cpu_pct
    if mem_pct is not None:
        payload["memory_percent"] = mem_pct
    if top_procs:
        payload["top_processes"] = top_procs[:5]

    if temp is not None and temp >= EMERGENCY_SNAPSHOT_TEMP_C:
        payload["critical_snapshot"] = collect_system_snapshot()

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


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        Webhook 告警上报                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def send_webhook(sensor: dict, value: float, recent_points: list) -> bool:
    """向 FastAPI /api/alerts/ingest 发送温度告警事件。"""
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


def send_process_exit_alert(process_name: str, crash_info: dict, snapshot: dict) -> bool:
    """向 FastAPI /api/alerts/ingest 发送进程异常退出告警。"""
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    crash_type = crash_info.get("crash_type", "unknown")
    stack_trace = crash_info.get("stack_trace", "")

    severity = "critical"
    alert_name = f"{process_name} 异常退出"

    evidence = {
        "crash_log": crash_info.get("raw_snippet", ""),
        "crash_type": crash_type,
        "monitored_process": process_name,
        "system_snapshot": snapshot,
    }

    # 如果有调用栈，提取第一行到 alert_name 中
    if stack_trace and stack_trace != "(未找到调用栈)":
        first_line = stack_trace.splitlines()[0].strip()
        alert_name = f"{process_name} 异常退出: {first_line}"

    payload = {
        "source": "process_monitor",
        "host": HOST_NAME,
        "severity": severity,
        "alert_name": alert_name,
        "metric": "process_exit",
        "value": 0,
        "threshold": 1,
        "duration_sec": 0,
        "sensor_id": process_name,
        "ts": ts,
        "evidence": evidence,
    }
    url = f"{FASTAPI_BASE_URL}/api/alerts/ingest"
    headers = {"Authorization": f"Bearer {ALERT_WEBHOOK_TOKEN}"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        print(f"[OK] 进程退出告警发送成功: {resp.json()}")
        return True
    except requests.RequestException as exc:
        print(f"[ERROR] 进程退出告警发送失败: {exc}")
        return False


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        启动自检                                             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def startup_check() -> None:
    """启动自检：检查上次是否异常退出，若是则打印死前快照内容。"""
    last_breath = EMERGENCY_SNAPSHOT_DIR / "last_breath.json"
    last_snapshot = EMERGENCY_SNAPSHOT_DIR / "last_snapshot.json"

    if last_breath.exists():
        try:
            with open(last_breath, encoding="utf-8") as f:
                data = json.load(f)
            temp = data.get("temperature", "N/A")
            ts = data.get("timestamp", "N/A")
            cpu = data.get("cpu_percent", "N/A")
            mem = data.get("memory_percent", "N/A")
            procs = data.get("top_processes", [])
            print()
            print("=" * 60)
            print("[启动自检] 检测到上次异常退出！死前快照：")
            print(f"  时间:     {ts}")
            print(f"  温度:     {temp}°C")
            print(f"  CPU:      {cpu}%")
            print(f"  内存:     {mem}%")
            print(f"  TOP 进程:")
            for p in procs[:5]:
                print(f"    {p.get('name', '?')}: CPU {p.get('cpu', 0)}%, MEM {p.get('memory', 0)}%")
            print("=" * 60)
            print()
            # 清理 last_breath，避免每次重启都打印
            last_breath.unlink()
        except Exception as e:
            print(f"[启动自检] 读取死前快照失败: {e}")

    if last_snapshot.exists():
        try:
            with open(last_snapshot, encoding="utf-8") as f:
                data = json.load(f)
            cpu = data.get("cpu_percent", "N/A")
            mem = data.get("memory_percent", "N/A")
            ts = data.get("timestamp", "N/A")
            print(f"[启动自检] 最近快照 ({ts}): CPU {cpu}%, 内存 {mem}%")
        except Exception:
            pass


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        主循环                                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def main() -> None:
    print(f"SuperBizAgent — OnCall 检测 Agent v2")
    print(f"  LHM URL:      {LHM_BASE_URL}")
    print(f"  FastAPI URL:  {FASTAPI_BASE_URL}")
    print(f"  温度监控:     {'开启' if ONCALL_TEMP_ENABLED else '关闭'}")
    if ONCALL_TEMP_ENABLED:
        print(f"  传感器匹配:   包含 '{LHM_SENSOR_NAME_CONTAINS}'")
        print(f"  告警阈值:     {ONCALL_TEMP_THRESHOLD_C}°C 连续 {ONCALL_TEMP_DURATION_SEC}s")
        print(f"  冷却期:       {ONCALL_TEMP_COOLDOWN_SEC}s")
    print(f"  轮询间隔:     {POLL_INTERVAL_SEC}s")
    if MONITOR_PROCESS:
        print(f"  进程监控:     {MONITOR_PROCESS}")
        if MONITOR_CRASH_DIR:
            print(f"  崩溃日志:     {MONITOR_CRASH_DIR}/{MONITOR_CRASH_PATTERN}")
    print()

    # 启动自检
    startup_check()

    # 温度告警状态
    breach_start: Optional[float] = None
    last_alert_at: Optional[float] = None
    recent_points: list = []

    # 进程监控状态
    last_process_pid: int | None = None
    last_crash_mtime: float = 0  # 避免重复发送同一崩溃日志的告警
    process_was_alive = False

    # 首次检测进程是否存活
    if MONITOR_PROCESS:
        proc = find_process(MONITOR_PROCESS)
        if proc:
            last_process_pid = proc["pid"]
            process_was_alive = True
            print(f"[进程监控] {MONITOR_PROCESS} 已在运行，PID={last_process_pid}")
        else:
            print(f"[进程监控] 未找到 {MONITOR_PROCESS}，等待进程启动...")

    # 启动标记
    running_flag = EMERGENCY_SNAPSHOT_DIR / ".running"
    EMERGENCY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    running_flag.touch()

    try:
        while True:
            # ── 1. 进程存活检测 ─────────────────────────────────────────
            if MONITOR_PROCESS:
                proc = find_process(MONITOR_PROCESS)
                if proc:
                    if not process_was_alive:
                        print(f"[进程监控] {MONITOR_PROCESS} 已启动，PID={proc['pid']}")
                    last_process_pid = proc["pid"]
                    process_was_alive = True
                elif process_was_alive:
                    # 进程消失了！上次还活着，这次没了 → 崩溃
                    print()
                    print(f"[进程监控] {MONITOR_PROCESS} 进程已消失！正在检查崩溃日志...")

                    crash_info: dict = {"crash_type": "unknown", "stack_trace": "", "raw_snippet": ""}
                    snapshot = collect_system_snapshot()

                    # 尝试读取崩溃日志
                    if MONITOR_CRASH_DIR and MONITOR_CRASH_PATTERN:
                        dump_path = find_latest_crash_dump(MONITOR_CRASH_DIR, MONITOR_CRASH_PATTERN)
                        if dump_path and os.path.getmtime(dump_path) > last_crash_mtime:
                            last_crash_mtime = os.path.getmtime(dump_path)
                            crash_info = parse_crash_dump(dump_path)
                            print(f"[进程监控] 崩溃日志: {dump_path}")
                            print(f"[进程监控] 类型: {crash_info['crash_type']}")
                            stack = crash_info.get("stack_trace", "")
                            if stack and stack != "(未找到调用栈)":
                                print(f"[进程监控] 调用栈:\n{stack[:500]}")
                        elif dump_path:
                            print(f"[进程监控] 崩溃日志未更新 ({dump_path})，可能不是本次崩溃")
                        else:
                            print(f"[进程监控] 未找到崩溃日志，可能进程是被手动结束的")

                    # 发送告警
                    send_process_exit_alert(MONITOR_PROCESS, crash_info, snapshot)

                    # 进程退出时也写一份紧急快照
                    emergency_snap = snapshot.copy()
                    emergency_snap["process_name"] = MONITOR_PROCESS
                    emergency_snap["crash_type"] = crash_info.get("crash_type")
                    emergency_snap["stack_trace"] = crash_info.get("stack_trace")
                    _write_snapshot = True
                    try:
                        EMERGENCY_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
                        path = EMERGENCY_SNAPSHOT_DIR / "last_breath.json"
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(emergency_snap, f, ensure_ascii=False, indent=2)
                    except OSError:
                        pass

                    last_process_pid = None
                    process_was_alive = False
                    print()

                # 记录当前进程系统占用
                if proc:
                    proc_cpu = proc.get("cpu_percent", 0)
                    proc_mem = proc.get("memory_percent", 0)
                else:
                    proc_cpu = None
                    proc_mem = None
            else:
                proc_cpu = None
                proc_mem = None

            # ── 2. 采集系统快照（每轮都做）────────────────────────────────
            snapshot = collect_system_snapshot()
            cpu_pct = snapshot.get("cpu_percent")
            mem_pct = snapshot.get("memory_percent")
            top_procs = snapshot.get("top_processes", [])

            # ── 3. 持续快照落盘 ──────────────────────────────────────────
            write_light_snapshot(snapshot)

            # ── 4. 读取温度传感器（仅在开关打开时）───────────────────
            current_temp_val: float | None = None
            if ONCALL_TEMP_ENABLED:
                sensor = fetch_temperature(LHM_SENSOR_NAME_CONTAINS)
                current_temp_val = sensor["value"] if sensor else None
            else:
                sensor = None

            # ── 5. 发送心跳（每轮都发，携带完整系统数据）─────────────────
            send_heartbeat(current_temp_val, recent_points, cpu_pct, mem_pct, top_procs)

            # ── 6. 温度告警检测（仅在开关打开时）───────────────────────
            ts_str = datetime.now().strftime("%H:%M:%S")
            status_parts: list[str] = []
            if MONITOR_PROCESS and proc:
                status_parts.append(f"{MONITOR_PROCESS}: CPU {proc_cpu:.0f}% MEM {proc_mem:.1f}%")
            if cpu_pct is not None:
                status_parts.append(f"SYS: CPU {cpu_pct:.0f}% MEM {mem_pct:.0f}%")

            if ONCALL_TEMP_ENABLED and sensor is not None:
                value = sensor["value"]
                recent_points.append([ts_str, value])
                if len(recent_points) > 60:
                    recent_points = recent_points[-60:]
                status_parts.insert(0, f"{sensor['name']}: {value:.1f}°C")

                now = time.monotonic()
                if value >= ONCALL_TEMP_THRESHOLD_C:
                    if breach_start is None:
                        breach_start = now
                        print(f" [超阈开始]", end="")
                    elapsed = now - breach_start
                    print(f" [持续 {elapsed:.0f}s / 需 {ONCALL_TEMP_DURATION_SEC}s]", end="")
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
                                breach_start = now
                        else:
                            remaining = ONCALL_TEMP_COOLDOWN_SEC - (now - last_alert_at)
                            print(f" [冷却中 剩余 {remaining:.0f}s]", end="")
                else:
                    if breach_start is not None:
                        print(f" [已恢复正常]", end="")
                    breach_start = None
            elif ONCALL_TEMP_ENABLED and sensor is None:
                print(f"[WARN] 未找到传感器 '{LHM_SENSOR_NAME_CONTAINS}'，LHM 是否已启动？")
                breach_start = None
                time.sleep(POLL_INTERVAL_SEC)
                continue
            else:
                breach_start = None

            print(f"[{ts_str}] {' | '.join(status_parts)}")
            time.sleep(POLL_INTERVAL_SEC)

    finally:
        # 正常退出时清理启动标记
        if running_flag.exists():
            running_flag.unlink()
        print("\nOnCall 检测 Agent 已停止")


if __name__ == "__main__":
    main()
