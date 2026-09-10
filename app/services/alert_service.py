"""内存告警服务：接收、去重、存储、发邮件、触发 AIOps 诊断、心跳监控"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from textwrap import dedent
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.config import config
from app.harness.repository import get_harness_repository
from app.models.alert import AlertIngestRequest, AlertRecord
from app.services.mail_service import build_alert_email_body, send_alert_email

if TYPE_CHECKING:
    from app.harness.repository import HarnessRepository


def build_alert_diagnosis_task(alert: AlertRecord) -> str:
    """Build the durable Agent task from the complete alert payload."""
    recent_pts = ""
    if alert.evidence and alert.evidence.recent_points:
        pts = alert.evidence.recent_points[-10:]
        recent_pts = "\n".join(f"  {point[0]}  {point[1]}" for point in pts)
        recent_pts = f"\n最近数据点（时间, 值）:\n{recent_pts}"

    crash_section = ""
    if alert.evidence:
        crash_type = alert.evidence.crash_type
        crash_log = alert.evidence.crash_log
        monitored_proc = alert.evidence.monitored_process
        snapshot = alert.evidence.system_snapshot
        if crash_type or crash_log:
            crash_section = "\n\n## 进程崩溃详情\n\n"
            if monitored_proc:
                crash_section += f"- 崩溃进程: {monitored_proc}\n"
            if crash_type:
                crash_section += f"- 崩溃类型: {crash_type}\n"
            if crash_log:
                crash_section += f"\n崩溃日志摘要:\n```\n{crash_log[:3000]}\n```\n"
            if snapshot and isinstance(snapshot, dict):
                cpu = snapshot.get("cpu_percent", "N/A")
                memory = snapshot.get("memory_percent", "N/A")
                crash_section += f"\n崩溃时系统状态: CPU {cpu}%, 内存 {memory}%\n"

    return dedent(
        f"""
        当前主机 [{alert.host}] 发生了一条 [{alert.severity.upper()}] 级别告警：

        告警名称: {alert.alert_name}
        指标:     {alert.metric}
        当前值:   {alert.value}
        阈值:     {alert.threshold}
        持续时间: {alert.duration_sec} 秒
        告警时间: {alert.ts}
        传感器ID: {alert.sensor_id or '未知'}{recent_pts}{crash_section}

        请基于以上真实告警数据，结合知识库经验和可用监控工具，
        分析告警根因并生成完整的诊断报告。报告格式要求同标准 AIOps 报告模板。
        报告必须分别列出：已观测事实、知识库证据、分析推断、处理建议。
        只有事实与证据能共同支持时才能给出确定根因；否则写“原因未确定”并列出待补充证据。
        """
    ).strip()


async def send_diagnosis_report_email(alert: AlertRecord, report: str) -> None:
    """Send the durable run result using the original alert context."""
    subject, body = build_alert_email_body(
        alert_name=alert.alert_name,
        host=alert.host,
        severity=alert.severity,
        metric=alert.metric,
        value=alert.value,
        threshold=alert.threshold,
        duration_sec=alert.duration_sec,
        ts=alert.ts,
        recent_points=alert.evidence.recent_points,
        diagnosis_report=report,
    )
    await asyncio.to_thread(send_alert_email, "[诊断报告] " + subject, body)


class AlertService:
    """内存告警服务（单例）。

    职责：
    - 接收 webhook 事件，对活跃告警去重（相同 alert_id 不重复处理）
    - 存储活跃告警列表，供 /api/alerts/active 查询
    - 发送首次告警邮件
    - 可选：为 critical 告警创建持久化 Harness Run
    - 心跳监控：检测主机失联，自动触发宕机告警
    """

    def __init__(
        self,
        repository_factory: Callable[[], HarnessRepository] = get_harness_repository,
    ) -> None:
        self._repository_factory = repository_factory
        self._alerts: dict[str, AlertRecord] = {}
        self._heartbeats: dict[str, datetime] = {}  # host → last heartbeat time
        self._hb_snapshots: dict[str, dict[str, Any]] = {}  # host → 最后快照
        self._down_hosts: set[str] = set()  # 当前已失联主机（防重复告警）
        self._checker_started: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest(self, req: AlertIngestRequest) -> tuple[AlertRecord, bool]:
        """接收一条告警事件。

        Returns:
            (record, is_new) — is_new=True 表示新告警（首次触发邮件）
        """
        alert_id = self._make_id(req)
        existing = self._alerts.get(alert_id)

        if existing and existing.status == "active":
            logger.info(f"告警 {alert_id} 已活跃，忽略重复上报")
            return existing, False

        record = AlertRecord(
            alert_id=alert_id,
            source=req.source,
            host=req.host,
            severity=req.severity,
            alert_name=req.alert_name,
            metric=req.metric,
            value=req.value,
            threshold=req.threshold,
            duration_sec=req.duration_sec,
            sensor_id=req.sensor_id,
            ts=req.ts,
            evidence=req.evidence,
        )
        self._alerts[alert_id] = record
        logger.info(f"新告警已记录: {alert_id} [{req.severity}] {req.alert_name}")

        # 发送首次告警邮件（在后台，不阻塞接口响应）
        asyncio.create_task(self._send_alert_email(record))

        return record, True

    async def trigger_diagnosis(self, record: AlertRecord) -> str | None:
        """Create a durable Harness Run for a critical alert."""
        if record.severity != "critical":
            return None
        if not config.oncall_auto_diagnosis:
            return None
        if record.diagnosis_run_id:
            return record.diagnosis_run_id

        repository = await asyncio.to_thread(self._repository_factory)
        run = await asyncio.to_thread(
            repository.create_run,
            task=build_alert_diagnosis_task(record),
            session_id=f"alert_{record.alert_id}",
            kind="alert_diagnosis",
            payload={"alert": record.model_dump(mode="json")},
        )
        record.diagnosis_run_id = str(run["id"])
        logger.info(
            f"告警 {record.alert_id} 已创建持久化诊断 Run: {record.diagnosis_run_id}"
        )
        return record.diagnosis_run_id

    def get_active_alerts(self) -> list[AlertRecord]:
        return [r for r in self._alerts.values() if r.status == "active"]

    def get_all_alerts(self) -> list[AlertRecord]:
        return list(self._alerts.values())

    def resolve(self, alert_id: str) -> AlertRecord | None:
        record = self._alerts.get(alert_id)
        if record:
            record.status = "resolved"
            logger.info(f"告警 {alert_id} 已标记为 resolved")
        return record

    # ------------------------------------------------------------------
    # Heartbeat / 心跳监控
    # ------------------------------------------------------------------

    def record_heartbeat(self, host: str, snapshot: dict[str, Any] | None = None) -> None:
        """Agent 进程定期上报心跳，服务端记录时间 + 快照并启动失联检测。

        snapshot 包含温度 / CPU / 内存 / 进程等，用于死因分析。
        """
        now = datetime.now()
        is_new_host = host not in self._heartbeats
        was_down = host in self._down_hosts

        self._heartbeats[host] = now
        if snapshot:
            self._hb_snapshots[host] = snapshot

        if was_down:
            self._down_hosts.discard(host)
            logger.info(f"[Heartbeat] 主机 {host} 恢复在线")

        if is_new_host:
            logger.info(f"[Heartbeat] 新主机注册: {host}")

        if not self._checker_started:
            self._checker_started = True
            asyncio.create_task(self._check_heartbeats())
            logger.info("[Heartbeat] 后台失联检测已启动")

    async def _check_heartbeats(self) -> None:
        """后台循环：每 30 秒检查所有主机心跳，超时则触发宕机告警。"""
        timeout = timedelta(seconds=config.oncall_heartbeat_timeout_sec)
        while True:
            await asyncio.sleep(30)
            now = datetime.now()
            for host, last_hb in list(self._heartbeats.items()):
                elapsed = now - last_hb
                if elapsed > timeout and host not in self._down_hosts:
                    # 主机失联！
                    self._down_hosts.add(host)
                    await self._on_host_down(host, elapsed)
                elif elapsed <= timeout and host in self._down_hosts:
                    # 主机恢复
                    self._down_hosts.discard(host)
                    await self._on_host_back(host)

    async def _on_host_down(self, host: str, elapsed: timedelta) -> None:
        """主机失联时：基于最后心跳快照分析死因，创建告警并发送邮件。"""
        alert_id = f"host-down-{host}"
        if alert_id in self._alerts and self._alerts[alert_id].status == "active":
            return

        last_snap = self._hb_snapshots.get(host, {})
        record = AlertRecord(
            alert_id=alert_id,
            source="heartbeat_monitor",
            host=host,
            severity="critical",
            alert_name=f"主机 {host} 失联",
            metric="heartbeat_timeout",
            value=elapsed.total_seconds(),
            threshold=float(config.oncall_heartbeat_timeout_sec),
            duration_sec=int(elapsed.total_seconds()),
            sensor_id="",
            ts=datetime.now().isoformat(),
        )
        self._alerts[alert_id] = record

        # ── 基于最后心跳快照构建死因分析 ──
        subject, body = build_alert_email_body(
            alert_name=record.alert_name,
            host=record.host,
            severity=record.severity,
            metric=record.metric,
            value=record.value,
            threshold=record.threshold,
            duration_sec=record.duration_sec,
            ts=record.ts,
            recent_points=[],
        )
        body += f"\n\n最后心跳时间: {self._heartbeats.get(host, '未知')}\n"

        if last_snap:
            crit = last_snap.get("critical_snapshot") or last_snap
            temp = crit.get("temperature") or last_snap.get("temperature")
            cpu = crit.get("cpu_percent")
            mem = crit.get("memory_percent")
            top_procs = crit.get("top_processes") or []
            recent = last_snap.get("recent_points") or crit.get("recent_points") or []
            threshold = last_snap.get("temp_threshold")

            body += "\n## 死因推断（基于最后心跳快照）\n\n"

            # 温度分析
            if temp is not None:
                body += f"**最后温度**: {temp}°C"
                if threshold and temp >= threshold:
                    body += f"（超过阈值 {threshold}°C）"
                body += "\n"

            if recent:
                trend = ", ".join(f"{t}: {v}°C" for t, v in recent[-5:])
                body += f"**温度走势**: {trend}\n"

                # 判断温度是否在上升
                vals = [v for _, v in recent if isinstance(v, (int, float))]
                if len(vals) >= 3 and vals[-1] >= vals[0] + 3:
                    body += "**趋势**: 温度持续上升，可能是散热失效或计算密集型负载\n"

            # CPU/内存
            if cpu is not None:
                body += f"\n**CPU 使用率**: {cpu}%\n"
            if mem is not None:
                body += f"**内存使用率**: {mem}%\n"

            # 嫌疑人进程
            if top_procs:
                body += "\n**高 CPU 进程（嫌疑人）**:\n"
                for p in top_procs[:5]:
                    body += f"- {p.get('name', '?')}: CPU {p.get('cpu', 0)}%\n"

            # 综合判断
            body += "\n## 最可能原因\n"
            if temp is not None and temp >= 85:
                body += f"- **过热关机**: 温度达到 {temp:.0f}°C，接近 CPU 保护断电阈值\n"
                if top_procs:
                    culprit = top_procs[0].get("name", "未知进程")
                    body += f"- **嫌疑进程**: {culprit}，可能是导致过热的元凶\n"
            elif cpu is not None and cpu > 90:
                body += "- **CPU 过载**: 使用率超过 90%，可能因负载过高导致系统无响应后强制断电\n"
            else:
                body += "- 非温度/CPU原因，可能是网络故障、主动关机或意外断电\n"

            body += "\n## 建议操作\n"
            body += "- 检查主机物理状态（风扇、散热器、环境温度）\n"
            body += "- 重启后查看 `memory/emergency/last_breath.json` 获取更多细节\n"
            if top_procs:
                body += f"- 考虑限制 {top_procs[0].get('name', '高负载进程')} 的资源使用\n"
        else:
            body += "\n## 死因推断\n\n无最后心跳数据，无法自动推断死因。可能原因：\n"
            body += "- 网络中断导致无法上报\n- 意外断电\n- Agent 进程异常崩溃\n"

        body += f"\n\n-- SuperBizAgent OnCall\n失联 {elapsed.total_seconds():.0f}s 自动触发"

        logger.warning(
            f"[Heartbeat] 主机 {host} 失联！超时 {elapsed.total_seconds():.0f}s > "
            f"阈值 {config.oncall_heartbeat_timeout_sec}s，最后温度: "
            f"{last_snap.get('temperature', 'N/A')}°C"
        )
        await asyncio.to_thread(send_alert_email, subject, body)
        logger.info(f"[Heartbeat] 失联告警邮件（含死因分析）已发送: {host}")

    async def _on_host_back(self, host: str) -> None:
        """主机恢复时：自动 resolve 失联告警。"""
        alert_id = f"host-down-{host}"
        if alert_id in self._alerts:
            self._alerts[alert_id].status = "resolved"
        logger.info(f"[Heartbeat] 主机 {host} 已恢复，失联告警已自动 resolve")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_id(req: AlertIngestRequest) -> str:
        """根据 host + sensor_id + alert_name 生成稳定 ID。"""
        raw = f"{req.host}|{req.sensor_id or req.alert_name}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    async def _send_alert_email(self, record: AlertRecord) -> None:
        try:
            subject, body = build_alert_email_body(
                alert_name=record.alert_name,
                host=record.host,
                severity=record.severity,
                metric=record.metric,
                value=record.value,
                threshold=record.threshold,
                duration_sec=record.duration_sec,
                ts=record.ts,
                recent_points=record.evidence.recent_points,
            )
            await asyncio.to_thread(send_alert_email, subject, body)
        except Exception as exc:
            logger.error(f"告警邮件后台发送失败: {exc}")

# 全局单例
alert_service = AlertService()
