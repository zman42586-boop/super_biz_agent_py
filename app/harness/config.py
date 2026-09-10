"""Configuration for the durable Agent runtime harness."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class HarnessSettings:
    """Small, isolated settings object so importing the harness never connects to MySQL."""

    database_url: str
    auto_create_schema: bool = True
    poll_interval_seconds: float = 1.0
    lease_seconds: int = 30
    tool_timeout_seconds: float = 10.0
    tool_max_retries: int = 2

    @classmethod
    def from_env(cls) -> HarnessSettings:
        explicit_url = os.getenv("HARNESS_DATABASE_URL", "").strip()
        if explicit_url:
            database_url = explicit_url
        else:
            user = os.getenv("HARNESS_MYSQL_USER", "superbiz")
            password = os.getenv("HARNESS_MYSQL_PASSWORD", "superbiz")
            host = os.getenv("HARNESS_MYSQL_HOST", "127.0.0.1")
            port = os.getenv("HARNESS_MYSQL_PORT", "3306")
            database = os.getenv("HARNESS_MYSQL_DATABASE", "superbiz_agent")
            database_url = (
                f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
                "?charset=utf8mb4"
            )

        return cls(
            database_url=database_url,
            auto_create_schema=_as_bool(os.getenv("HARNESS_AUTO_CREATE_SCHEMA", "true"), True),
            poll_interval_seconds=float(os.getenv("HARNESS_POLL_INTERVAL_SEC", "1")),
            lease_seconds=int(os.getenv("HARNESS_LEASE_SEC", "30")),
            tool_timeout_seconds=float(os.getenv("HARNESS_TOOL_TIMEOUT_SEC", "10")),
            tool_max_retries=int(os.getenv("HARNESS_TOOL_MAX_RETRIES", "2")),
        )


harness_settings = HarnessSettings.from_env()
