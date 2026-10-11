import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, inspect, select, text

from app.harness.evidence import redact
from app.harness.log_collector import IncrementalLogCollector
from app.harness.monitoring import HostMonitoringRepository
from app.harness.monitoring_models import Notification
from app.harness.notifications import dispatch_notifications
from app.harness.repository import HarnessRepository, LeaseLostError
from scripts.migrate_harness_v2 import migrate


def test_collector_restart_retry_rotation_truncation_and_partial_line(tmp_path):
    log = tmp_path / "matlab.log"
    state = tmp_path / "cursor.json"
    log.write_text(
        "ordinary\nERROR MATLAB:nomem token=secret\n at function.m:8\n", encoding="utf-8"
    )
    collector = IncrementalLogCollector([str(tmp_path / "matlab.log*")], state)
    collector.collect()
    original = collector.batch()
    assert len(original) == 2
    assert "secret" not in json.dumps(original)
    # Failed HTTP delivery keeps the same event IDs across restart.
    collector = IncrementalLogCollector([str(tmp_path / "matlab.log*")], state)
    collector.collect()
    assert collector.batch() == original
    collector.acknowledge(original)
    log.rename(tmp_path / "matlab.log.1")
    log.write_text("WARNING rotated\n", encoding="utf-8")
    collector.collect()
    rotated = collector.batch()
    assert len(rotated) == 1 and "rotated" in rotated[0]["text"]
    assert rotated[0]["event_id"] not in {e["event_id"] for e in original}
    collector.acknowledge(rotated)
    log.write_text("ERROR truncated\n", encoding="utf-8")
    collector.collect()
    truncated = collector.batch()
    assert len(truncated) == 1 and "truncated" in truncated[0]["text"]
    collector.acknowledge(truncated)
    with log.open("a", encoding="utf-8") as stream:
        stream.write("WARNING incomplete")
    collector.collect()
    assert collector.batch() == []
    with log.open("a", encoding="utf-8") as stream:
        stream.write(" completed\n")
    collector.collect()
    assert "incomplete completed" in collector.batch()[0]["text"]


def test_collector_spool_and_read_budget_are_bounded(tmp_path):
    log = tmp_path / "matlab.log"
    log.write_text("ERROR many\n" * 1000, encoding="utf-8")
    state = tmp_path / "cursor.json"
    collector = IncrementalLogCollector(
        [str(log)], state, read_bytes=1000, spool_entries=3, spool_bytes=2048
    )
    collector.collect()
    assert len(collector.batch()) <= 3
    assert collector.state["dropped"] > 0
    assert next(iter(collector.state["files"].values()))["offset"] <= 1000
    assert len(json.dumps(collector.state["pending"]).encode()) <= 2048


def test_initially_empty_log_can_detect_same_size_copytruncate(tmp_path):
    log = tmp_path / "empty.log"
    log.touch()
    collector = IncrementalLogCollector([str(log)], tmp_path / "cursor.json")
    collector.collect()
    log.write_text("ERROR first\n", encoding="utf-8")
    collector.collect()
    batch = collector.batch()
    assert len(batch) == 1
    collector.acknowledge(batch)
    log.write_text("ERROR other\n", encoding="utf-8")
    collector.collect()
    assert len(collector.batch()) == 1
    assert "other" in collector.batch()[0]["text"]


def test_redaction_covers_common_secrets_and_personal_fields():
    value = redact(
        'Authorization=Bearer private-value {"api_key":"secret-key"} '
        "password=pw SMTP_PASS=smtp-value sk-1234567890 "
        "john@example.com C:\\Users\\Jane\\runtime.log"
    )
    for secret in ["private-value", "secret-key", "smtp-value", "1234567890", "john@", "Jane"]:
        assert secret not in value


@pytest.mark.parametrize("previous_epoch_column", [False, True])
def test_additive_migration_preserves_old_run_and_is_idempotent(tmp_path, previous_epoch_column):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'migration.sqlite').as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, status VARCHAR(32), state_json JSON, version INTEGER NOT NULL DEFAULT 0"
                + (", lease_epoch BIGINT NOT NULL DEFAULT 0" if previous_epoch_column else "")
                + ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, status, state_json, version) VALUES ('legacy', 'interrupted', '{\"plan\":[\"old\"]}', 37)"
            )
        )
    migrate(engine)
    migrate(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("agent_runs")}
    assert "version" in columns
    assert ("lease_epoch" in columns) == previous_epoch_column
    assert {"monitored_hosts", "host_evidence", "harness_notifications"}.issubset(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT state_json, version FROM agent_runs WHERE id='legacy'")
        ).first()
        assert "old" in row[0] and row[1] == 37


@pytest.mark.asyncio
async def test_result_outbox_is_atomic_and_has_one_automatic_delivery(tmp_path, monkeypatch):
    repository = HarnessRepository(f"sqlite+pysqlite:///{(tmp_path / 'mail.sqlite').as_posix()}")
    monitor = HostMonitoringRepository(repository)
    monitor.record_heartbeat("pc", {})
    from app.harness.monitoring_models import MonitoredHost

    with repository._session_factory.begin() as session:
        row = session.get(MonitoredHost, "pc")
        row.last_heartbeat_at = repository.db_now(session) - timedelta(seconds=100)
    run_id = monitor.scan()[0]
    run = repository.claim_next_run("B", 30)
    with pytest.raises(LeaseLostError):
        repository.finish_run(run_id, "stale", worker_id="A", version=0)
    with repository._session_factory() as session:
        assert session.get(Notification, f"diagnosis:{run_id}") is None
    repository.finish_run(run_id, "immutable report", worker_id="B", version=run["version"])
    sender = AsyncMock()
    monkeypatch.setattr("app.services.alert_service.send_diagnosis_report_email", sender)
    monkeypatch.setattr("app.services.mail_service.send_alert_email", lambda *args: None)
    await asyncio.gather(*(dispatch_notifications(repository) for _ in range(8)))
    sender.assert_awaited_once()
    with repository._session_factory() as session:
        assert session.get(Notification, f"diagnosis:{run_id}").status == "sent"


@pytest.mark.asyncio
async def test_ambiguous_smtp_and_dead_sender_are_not_retried(tmp_path, monkeypatch):
    repository = HarnessRepository(
        f"sqlite+pysqlite:///{(tmp_path / 'ambiguous.sqlite').as_posix()}"
    )
    monitor = HostMonitoringRepository(repository)
    monitor.record_heartbeat("pc", {})
    from app.harness.monitoring_models import MonitoredHost

    with repository._session_factory.begin() as session:
        session.get(MonitoredHost, "pc").last_heartbeat_at = repository.db_now(session) - timedelta(
            seconds=100
        )
    monitor.scan()
    attempts = []

    def ambiguous(*args):
        attempts.append(args)
        raise RuntimeError("SMTP connection lost after DATA")

    monkeypatch.setattr("app.services.mail_service.send_alert_email", ambiguous)
    await dispatch_notifications(repository)
    await dispatch_notifications(repository)
    assert len(attempts) == 1
    with repository._session_factory.begin() as session:
        notice = session.scalar(select(Notification))
        assert notice.status == "uncertain"
        notice.status = "sending"
        notice.claimed_at = repository.db_now(session) - timedelta(seconds=121)
    await dispatch_notifications(repository)
    assert len(attempts) == 1
    with repository._session_factory() as session:
        assert session.scalar(select(Notification)).status == "uncertain"
