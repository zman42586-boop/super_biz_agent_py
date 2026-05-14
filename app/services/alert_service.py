"""内存告警服务：接收、去重、存储、发邮件、触发 AIOps 诊断"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from app.config import config
from app.models.alert import AlertIngestRequest, AlertRecord
from app.services.mail_service import build_alert_email_body, send_alert_email


class AlertService:
    """内存告警服务（单例）。

    职责：
    - 接收 webhook 事件，对活跃告警去重（相同 alert_id 不重复处理）
    - 存储活跃告警列表，供 /api/alerts/active 查询
    - 发送首次告警邮件
    - 可选：后台异步触发 AIOps 诊断并把报告追加到第二封邮件
    """

    def __init__(self) -> None:
        self._alerts: Dict[str, AlertRecord] = {}

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

    async def trigger_diagnosis(self, record: AlertRecord) -> None:
        """后台触发 AIOps 诊断并发送诊断报告邮件（仅 critical）。"""
        if record.severity != "critical":
            return
        if not config.oncall_auto_diagnosis:
            return
        asyncio.create_task(self._run_diagnosis(record))

    def get_active_alerts(self) -> List[AlertRecord]:
        return [r for r in self._alerts.values() if r.status == "active"]

    def get_all_alerts(self) -> List[AlertRecord]:
        return list(self._alerts.values())

    def resolve(self, alert_id: str) -> Optional[AlertRecord]:
        record = self._alerts.get(alert_id)
        if record:
            record.status = "resolved"
            logger.info(f"告警 {alert_id} 已标记为 resolved")
        return record

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

    async def _run_diagnosis(self, record: AlertRecord) -> None:
        """在后台运行 AIOps 诊断，完成后把报告更新到 record 并发第二封邮件。"""
        try:
            from app.services.aiops_service import aiops_service

            logger.info(f"开始对告警 {record.alert_id} 自动 AIOps 诊断...")
            report = await aiops_service.execute_alert_diagnosis(record)
            record.diagnosis_report = report

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
                diagnosis_report=report,
            )
            subject = "[诊断报告] " + subject
            await asyncio.to_thread(send_alert_email, subject, body)
            logger.info(f"诊断报告邮件已发送，alert_id={record.alert_id}")
        except Exception as exc:
            logger.error(f"AIOps 自动诊断失败: {exc}")


# 全局单例
alert_service = AlertService()
