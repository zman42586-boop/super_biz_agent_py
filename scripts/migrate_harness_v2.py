"""Idempotent additive migration. Stop API and ALL workers before running it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect

from app.harness import monitoring_models  # noqa: F401 -- register new tables
from app.harness.config import harness_settings
from app.harness.models import Base


def migrate(engine):
    inspector = inspect(engine)
    if inspector.has_table("agent_runs"):
        columns = {column["name"] for column in inspector.get_columns("agent_runs")}
        if "version" not in columns:
            raise RuntimeError(
                "agent_runs.version is missing; apply the original Harness schema first"
            )
        # Keep all existing versions and any previously deployed lease_epoch.
        # No active worker may span the change of token semantics. The operator
        # drains/stops all old workers before migration and restart (see docs).
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    engine = create_engine(harness_settings.database_url)
    try:
        migrate(engine)
        print("Harness v2 migration complete. Existing checkpoints and runs preserved.")
    finally:
        engine.dispose()
