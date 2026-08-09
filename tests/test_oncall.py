"""OnCall 核心逻辑聚焦测试（不发真实邮件，不依赖 LHM 运行）"""

from __future__ import annotations

import smtplib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


# ── 1. 告警模型校验 ──────────────────────────────────────────────────────

def test_alert_ingest_request_valid():
    from app.models.alert import AlertEvidence, AlertIngestRequest

    req = AlertIngestRequest(
        source="librehardwaremonitor",
        host="MY-PC",
        severity="critical",
        alert_name="CPU Package Temperature High",
        metric="temperature_c",
        value=86.5,
        threshold=60.0,
        duration_sec=65,
        sensor_id="lhm://cpu/pkg",
        ts="2026-05-08T00:00:00+08:00",
        evidence=AlertEvidence(
            lhm_url="http://127.0.0.1:8085/data.json",
            recent_points=[["00:00:00", 86.5]],
        ),
    )
    assert req.value == 86.5
    assert req.severity == "critical"


def test_alert_record_defaults():
    from app.models.alert import AlertEvidence, AlertRecord

    rec = AlertRecord(
        alert_id="abc123",
        source="lhm",
        host="PC",
        severity="warning",
        alert_name="Test",
        metric="temperature_c",
        value=65.0,
        threshold=60.0,
        duration_sec=60,
        sensor_id="s1",
        ts="2026-05-08T00:00:00+08:00",
        evidence=AlertEvidence(),
    )
    assert rec.status == "active"
    assert rec.diagnosis_report is None


# ── 2. config 中 SMTP 字段 ──────────────────────────────────────────────

def test_config_smtp_fields():
    from app.config import config

    assert config.smtp_host == "smtp.163.com"
    assert config.smtp_port == 465
    assert isinstance(config.smtp_user, str)
    assert config.oncall_temp_threshold_c == 85.0
    assert config.oncall_temp_duration_sec == 60
    assert config.oncall_temp_cooldown_sec == 120


# ── 3. SMTP 发送服务（mock SMTP_SSL，不真实发送）──────────────────────

def test_send_alert_email_success(monkeypatch):
    from app.services.mail_service import send_alert_email
    from app.services import mail_service

    monkeypatch.setattr(mail_service.config, "smtp_user", "sender@example.com")
    monkeypatch.setattr(mail_service.config, "smtp_pass", "test-password")
    monkeypatch.setattr(mail_service.config, "smtp_to", "receiver@example.com")

    mock_server = MagicMock()
    mock_ctx = MagicMock()

    with patch("app.services.mail_service.ssl.create_default_context", return_value=mock_ctx):
        with patch("app.services.mail_service.smtplib.SMTP_SSL") as mock_ssl:
            mock_ssl.return_value.__enter__ = lambda s: mock_server
            mock_ssl.return_value.__exit__ = MagicMock(return_value=False)

            send_alert_email(
                subject="[TEST] Alert",
                body="Test body",
            )

    mock_server.ehlo.assert_called_once()
    mock_server.login.assert_called_once()
    mock_server.send_message.assert_called_once()


def test_send_alert_email_missing_config(monkeypatch):
    from app.services import mail_service

    monkeypatch.setattr(mail_service.config, "smtp_user", "")
    with pytest.raises(RuntimeError, match="SMTP 配置不完整"):
        mail_service.send_alert_email("subj", "body")


# ── 4. 邮件正文构造 ─────────────────────────────────────────────────────

def test_build_alert_email_body():
    from app.services.mail_service import build_alert_email_body

    subject, body = build_alert_email_body(
        alert_name="CPU Package Temperature High",
        host="MY-PC",
        severity="critical",
        metric="temperature_c",
        value=86.5,
        threshold=60.0,
        duration_sec=65,
        ts="2026-05-08T00:00:00+08:00",
        recent_points=[["00:00:00", 85.0], ["00:00:05", 86.5]],
    )
    assert "CRITICAL" in subject
    assert "86.5" in subject
    assert "60.0" in subject
    assert "00:00:05" in body


def test_build_alert_email_body_with_report():
    from app.services.mail_service import build_alert_email_body

    _, body = build_alert_email_body(
        alert_name="Test",
        host="PC",
        severity="warning",
        metric="temperature_c",
        value=65.0,
        threshold=60.0,
        duration_sec=60,
        ts="2026-05-08T00:00:00+08:00",
        recent_points=[],
        diagnosis_report="## 诊断报告\n根因：散热问题",
    )
    assert "诊断报告" in body
    assert "散热问题" in body


# ── 5. AlertService 去重逻辑 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_service_dedup():
    """相同 host+sensor_id 的活跃告警不应重复处理。"""
    from app.models.alert import AlertEvidence, AlertIngestRequest
    from app.services.alert_service import AlertService

    svc = AlertService()

    req = AlertIngestRequest(
        source="lhm",
        host="PC",
        severity="warning",
        alert_name="Temp High",
        metric="temperature_c",
        value=65.0,
        threshold=60.0,
        duration_sec=60,
        sensor_id="s1",
        ts="2026-05-08T00:00:00+08:00",
        evidence=AlertEvidence(),
    )

    with patch(
        "app.services.alert_service.asyncio.create_task",
        side_effect=lambda coroutine: (coroutine.close(), MagicMock())[1],
    ):
        rec1, is_new1 = await svc.ingest(req)
        rec2, is_new2 = await svc.ingest(req)

    assert is_new1 is True
    assert is_new2 is False
    assert len(svc.get_active_alerts()) == 1


@pytest.mark.asyncio
async def test_alert_service_resolve():
    from app.models.alert import AlertEvidence, AlertIngestRequest
    from app.services.alert_service import AlertService

    svc = AlertService()
    req = AlertIngestRequest(
        source="lhm",
        host="PC2",
        severity="critical",
        alert_name="Temp High",
        metric="temperature_c",
        value=90.0,
        threshold=60.0,
        duration_sec=60,
        sensor_id="s2",
        ts="2026-05-08T00:00:00+08:00",
        evidence=AlertEvidence(),
    )
    with patch(
        "app.services.alert_service.asyncio.create_task",
        side_effect=lambda coroutine: (coroutine.close(), MagicMock())[1],
    ):
        rec, _ = await svc.ingest(req)

    resolved = svc.resolve(rec.alert_id)
    assert resolved is not None
    assert resolved.status == "resolved"
    assert len(svc.get_active_alerts()) == 0


# ── 6. LHM 传感器解析（使用 fixture JSON）─────────────────────────────

def test_lhm_sensor_parser():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from lhm_alert_agent import _find_sensors

    # 模拟 LHM data.json 的树形结构
    fixture = {
        "Text": "Computer",
        "Type": "Computer",
        "Children": [
            {
                "Text": "AMD Ryzen 9",
                "Type": "Cpu",
                "Children": [
                    {
                        "Text": "CCDs Max (Tdie)",
                        "Type": "Temperature",
                        "Value": "72.3 °C",
                        "id": "42",
                    },
                    {
                        "Text": "Core #1",
                        "Type": "Temperature",
                        "Value": "68.1 °C",
                        "id": "43",
                    },
                ],
            }
        ],
    }

    results: list = []
    _find_sensors(fixture, "CCDs Max (Tdie)", results)

    assert len(results) == 1
    assert results[0]["value"] == pytest.approx(72.3)
    assert "CCDs Max" in results[0]["name"]
