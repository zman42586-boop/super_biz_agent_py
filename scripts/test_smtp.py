import os
import smtplib
import socket
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv


def _require_env(name: str) -> str:
    value = os.getenv(name, "")
    if value is None:
        value = ""
    value = value.strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _tcp_connect_check(host: str, port: int, timeout_s: float = 5.0) -> Tuple[str, str]:
    """Return (ip, info) if TCP connect succeeds."""
    ip = socket.gethostbyname(host)
    with socket.create_connection((host, port), timeout=timeout_s):
        pass
    return ip, f"TCP connect ok ({host}:{port})"


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env", override=False)

    host = _require_env("SMTP_HOST")
    port = int(_require_env("SMTP_PORT"))
    user = _require_env("SMTP_USER")
    password = _require_env("SMTP_PASS")  # do not print
    mail_from = os.getenv("SMTP_FROM", "").strip() or user
    mail_to = _require_env("SMTP_TO")

    # Basic connectivity check first (faster feedback than waiting for SMTP timeouts).
    ip, info = _tcp_connect_check(host, port)
    print(f"[OK] DNS/TCP: {info}, resolved_ip={ip}")

    now = datetime.now(timezone.utc).astimezone()
    subject = f"[SuperBizAgent SMTP Test] {now.isoformat(timespec='seconds')}"
    body = "\n".join(
        [
            "This is a test email sent by SuperBizAgent.",
            "",
            f"timestamp={now.isoformat(timespec='seconds')}",
            f"smtp_host={host}",
            f"smtp_port={port}",
            f"from={mail_from}",
            f"to={mail_to}",
        ]
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content(body)

    context = ssl.create_default_context()

    print("[INFO] Connecting SMTP over SSL...")
    with smtplib.SMTP_SSL(host=host, port=port, context=context, timeout=15) as server:
        server.set_debuglevel(0)

        print("[INFO] EHLO...")
        server.ehlo()

        print("[INFO] Logging in...")
        server.login(user, password)

        print("[INFO] Sending email...")
        server.send_message(msg)

    print("[OK] SMTP send succeeded. Check your inbox (and spam folder).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

