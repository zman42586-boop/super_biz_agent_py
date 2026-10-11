"""At-most-one automatic SMTP attempt per notification, including ambiguous outcomes."""

import asyncio
import uuid
from datetime import timedelta

from loguru import logger
from sqlalchemy import select, update

from app.harness.monitoring_models import Notification


def claim_notification(repository):
    with repository._session_factory.begin() as session:
        now = repository.db_now(session)
        # A dead sender may have already submitted DATA. Never automatically resend it.
        session.execute(
            update(Notification)
            .where(
                Notification.status == "sending",
                Notification.claimed_at < now - timedelta(seconds=120),
            )
            .values(
                status="uncertain", last_error="Sender disappeared; verify SMTP delivery manually."
            )
        )
        row = session.scalar(
            select(Notification)
            .where(Notification.status == "pending")
            .order_by(Notification.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        token = str(uuid.uuid4())
        result = session.execute(
            update(Notification)
            .where(
                Notification.id == row.id,
                Notification.status == "pending",
            )
            .values(status="sending", delivery_token=token, claimed_at=now)
        )
        if result.rowcount != 1:
            return None
        return row.id, token, row.run_id, row.payload_json


async def dispatch_notifications(repository, limit=10):
    from app.models.alert import AlertRecord
    from app.services.alert_service import send_diagnosis_report_email
    from app.services.mail_service import build_alert_email_body, send_alert_email

    for _ in range(limit):
        claimed = await asyncio.to_thread(claim_notification, repository)
        if claimed is None:
            return
        key, token, run_id, payload = claimed
        outcome, error = "sent", None
        try:
            alert = AlertRecord.model_validate(payload["alert"])
            if "report" in payload:
                await send_diagnosis_report_email(alert, payload["report"])
            else:
                subject, body = build_alert_email_body(
                    alert.alert_name,
                    alert.host,
                    alert.severity,
                    alert.metric,
                    alert.value,
                    alert.threshold,
                    alert.duration_sec,
                    alert.ts,
                    [],
                )
                body += "\n原因未确定。心跳失联可能由网络、采集器或主机故障引起；AI 诊断随后交付。"
                await asyncio.to_thread(send_alert_email, subject, body)
        except Exception as exc:
            outcome, error = "uncertain", str(exc)[:1000]
            logger.error(f"[Notifications] {key}: {outcome}: {error}")
        # Results are attached to immutable completed runs, independent of worker lease.
        with repository._session_factory.begin() as session:
            result = session.execute(
                update(Notification)
                .where(
                    Notification.id == key,
                    Notification.status == "sending",
                    Notification.delivery_token == token,
                )
                .values(status=outcome, last_error=error)
            )
            if result.rowcount == 1 and run_id:
                repository._append_event(
                    session,
                    run_id,
                    "alert_diagnosis_email_sent" if outcome == "sent" else "notification_uncertain",
                    {"notification_id": key, "status": outcome},
                )
