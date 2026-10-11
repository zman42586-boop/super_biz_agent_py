"""进程告警服务与持久化主机心跳监控，复用 Harness 诊断链路。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
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
        传感器ID: {alert.sensor_id or "未知"}{recent_pts}{crash_section}

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
    """进程告警保存在内存，主机监测与失联告警持久化到 MySQL。

    职责：
    - 接收 webhook 事件，对活跃告警去重（相同 alert_id 不重复处理）
    - 存储活跃告警列表，供 /api/alerts/active 查询
    - 发送首次告警邮件
    - 可选：为 critical 告警创建持久化 Harness Run
    - 心跳监控：持久化失联事件、即时通知和降级诊断任务
    """

    def __init__(
        self,
        repository_factory: Callable[[], HarnessRepository] = get_harness_repository,
    ) -> None:
        self._repository_factory = repository_factory
        self._alerts: dict[str, AlertRecord] = {}
        self._checker_task: asyncio.Task | None = None

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
        logger.info(f"告警 {record.alert_id} 已创建持久化诊断 Run: {record.diagnosis_run_id}")
        return record.diagnosis_run_id

    def get_active_alerts(self) -> list[AlertRecord]:
        return [
            r for r in self._alerts.values() if r.status == "active"
        ] + self._monitoring().alerts(True)

    def get_all_alerts(self) -> list[AlertRecord]:
        return list(self._alerts.values()) + self._monitoring().alerts()

    def resolve(self, alert_id: str) -> AlertRecord | None:
        record = self._alerts.get(alert_id)
        if record:
            record.status = "resolved"
            logger.info(f"告警 {alert_id} 已标记为 resolved")
        return record

    # ------------------------------------------------------------------
    # Heartbeat / 心跳监控
    # ------------------------------------------------------------------

    def _monitoring(self):
        from app.harness.monitoring import HostMonitoringRepository

        return HostMonitoringRepository(self._repository_factory())

    async def record_heartbeat(self, host: str, snapshot: dict[str, Any] | None = None) -> None:
        monitoring = await asyncio.to_thread(self._monitoring)
        previous = await asyncio.to_thread(monitoring.record_heartbeat, host, snapshot or {})
        if previous != "online":
            logger.info(f"[Heartbeat] host recovered: {host}")

    async def record_logs(self, host, entries):
        monitoring = await asyncio.to_thread(self._monitoring)
        await asyncio.to_thread(monitoring.record_logs, host, entries)

    def start_heartbeat_monitor(self):
        if self._checker_task is None or self._checker_task.done():
            self._checker_task = asyncio.create_task(self._check_heartbeats())

    async def stop_heartbeat_monitor(self):
        if self._checker_task:
            self._checker_task.cancel()
            await asyncio.gather(self._checker_task, return_exceptions=True)
            self._checker_task = None

    async def check_heartbeats_once(self):
        from app.harness.notifications import dispatch_notifications

        repository = await asyncio.to_thread(self._repository_factory)
        from app.harness.monitoring import HostMonitoringRepository

        created = await asyncio.to_thread(HostMonitoringRepository(repository).scan)
        await dispatch_notifications(repository)
        return created

    async def _check_heartbeats(self):
        # Start at service boot, including hosts registered before this process existed.
        while True:
            try:
                await self.check_heartbeats_once()
            except Exception:
                logger.exception("[Heartbeat] check failed; will retry")
            await asyncio.sleep(config.oncall_heartbeat_check_interval_sec)

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
