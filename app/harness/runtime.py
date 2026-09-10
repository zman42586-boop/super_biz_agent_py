"""Context propagated from a durable Run into LangGraph nodes and tools."""

from __future__ import annotations

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


_run_context: ContextVar[HarnessRunContext | None] = ContextVar(
    "harness_run_context", default=None
)


def get_run_context() -> HarnessRunContext | None:
    return _run_context.get()


@contextmanager
def harness_run_context(context: HarnessRunContext) -> Iterator[None]:
    token = _run_context.set(context)
    try:
        yield
    finally:
        _run_context.reset(token)
