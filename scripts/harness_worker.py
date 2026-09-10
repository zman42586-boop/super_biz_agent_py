"""Start the durable Agent Harness worker."""

import asyncio

from app.harness.worker import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
