"""Bounded host evidence and durable notifications, sharing the Harness database."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.harness.models import Base, utc_now


class MonitoredHost(Base):
    __tablename__ = "monitored_hosts"
    host: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    outage_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outage_run_id: Mapped[str | None] = mapped_column(String(36))
    alert_json: Mapped[dict | None] = mapped_column(JSON)


class HostEvidence(Base):
    __tablename__ = "host_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host: Mapped[str] = mapped_column(
        String(128), ForeignKey("monitored_hosts.host"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    event_key: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        Index("idx_host_evidence_host_kind_time", "host", "kind", "received_at"),
        UniqueConstraint("host", "kind", "event_key", name="uk_host_evidence_event"),
    )


class Notification(Base):
    __tablename__ = "harness_notifications"
    id: Mapped[str] = mapped_column(String(192), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agent_runs.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    delivery_token: Mapped[str | None] = mapped_column(String(36))
    last_error: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
