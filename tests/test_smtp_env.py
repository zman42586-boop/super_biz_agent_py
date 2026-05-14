import os
from pathlib import Path

from dotenv import load_dotenv


def test_smtp_env_present() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env", override=False)

    required = [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASS",
        "SMTP_TO",
    ]

    missing = [k for k in required if not (os.getenv(k) or "").strip()]
    assert not missing, f"Missing env vars: {missing}"

    port = int((os.getenv("SMTP_PORT") or "").strip())
    assert 1 <= port <= 65535

