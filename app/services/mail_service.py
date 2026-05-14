"""SMTP 邮件发送服务（163 SSL）"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from loguru import logger

from app.config import config


def send_alert_email(
    subject: str,
    body: str,
    to: Optional[str] = None,
) -> None:
    """发送告警邮件。

    Args:
        subject: 邮件主题
        body: 邮件正文（纯文本）
        to: 收件人，默认使用 config.smtp_to

    Raises:
        RuntimeError: SMTP 配置缺失或发送失败
    """
    smtp_host = config.smtp_host
    smtp_port = config.smtp_port
    smtp_user = config.smtp_user.strip()
    smtp_pass = config.smtp_pass.strip()
    mail_from = config.smtp_from.strip() or smtp_user
    mail_to = (to or config.smtp_to).strip()

    if not smtp_user or not smtp_pass or not mail_to:
        raise RuntimeError(
            "SMTP 配置不完整，请检查 .env 中的 SMTP_USER / SMTP_PASS / SMTP_TO"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content(body)

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(host=smtp_host, port=smtp_port, context=ctx, timeout=15) as server:
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info(f"告警邮件已发送至 {mail_to}，主题: {subject}")
    except Exception as exc:
        logger.error(f"告警邮件发送失败: {exc}")
        raise RuntimeError(f"告警邮件发送失败: {exc}") from exc


def build_alert_email_body(alert_name: str, host: str, severity: str,
                            metric: str, value: float, threshold: float,
                            duration_sec: int, ts: str,
                            recent_points: list,
                            diagnosis_report: Optional[str] = None) -> tuple[str, str]:
    """构造告警邮件的主题和正文。

    Returns:
        (subject, body) tuple
    """
    subject = (
        f"[{severity.upper()}][{host}] {alert_name} "
        f"({value:.1f} > {threshold:.1f} for {duration_sec}s)"
    )

    lines = [
        "=" * 60,
        f"告警名称: {alert_name}",
        f"严重等级: {severity.upper()}",
        f"主机:     {host}",
        f"指标:     {metric}",
        f"当前值:   {value:.1f}",
        f"阈值:     {threshold:.1f}",
        f"持续时间: {duration_sec} 秒",
        f"告警时间: {ts}",
        "=" * 60,
    ]

    if recent_points:
        lines.append("")
        lines.append("最近数据点（时间, 值）:")
        for pt in recent_points[-10:]:
            lines.append(f"  {pt[0]}  {pt[1]}")

    if diagnosis_report:
        lines.append("")
        lines.append("=" * 60)
        lines.append("AIOps 诊断报告:")
        lines.append("=" * 60)
        lines.append(diagnosis_report)

    lines.append("")
    lines.append(f"-- SuperBizAgent OnCall")

    return subject, "\n".join(lines)
