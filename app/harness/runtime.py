"""Context propagated from a durable Run into LangGraph nodes and tools."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.harness.repository import HarnessRepository


@dataclass(frozen=True)
class HarnessRunContext:
    run_id: str
    repository: HarnessRepository
    worker_id: str = ""
    version: int = 0
    offline_host: str | None = None
    evidence: dict | None = None


_run_context: ContextVar[HarnessRunContext | None] = ContextVar("harness_run_context", default=None)


def get_run_context() -> HarnessRunContext | None:
    return _run_context.get()


async def ensure_run_ownership() -> None:
    """Preflight external work; durable writes still require transactional fencing."""
    context = get_run_context()
    if context is not None and not await asyncio.to_thread(
        context.repository.check_ownership,
        context.run_id,
        context.worker_id,
        context.version,
    ):
        from app.harness.repository import LeaseLostError

        raise LeaseLostError("Run ownership lost before starting the next operation")


@contextmanager
def harness_run_context(context: HarnessRunContext) -> Iterator[None]:
    token = _run_context.set(context)
    try:
        yield
    finally:
        _run_context.reset(token)
