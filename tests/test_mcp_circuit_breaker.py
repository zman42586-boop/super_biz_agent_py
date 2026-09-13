from __future__ import annotations

import pytest

from app.agent.mcp_client import MCPCircuitOpenError, MCPToolProvider


class FakeClient:
    def __init__(self, *, tools=None, error: Exception | None = None) -> None:
        self.tools = tools or []
        self.error = error
        self.calls = 0

    async def get_tools(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.tools


@pytest.mark.asyncio
async def test_successful_discovery_is_cached() -> None:
    provider = MCPToolProvider(failure_threshold=1, cooldown_seconds=30)
    client = FakeClient(tools=["memory", "cpu"])

    async def factory():
        return client

    assert await provider.get_tools(factory) == ["memory", "cpu"]
    assert await provider.get_tools(factory) == ["memory", "cpu"]
    assert client.calls == 1
    assert provider.state == "closed"


@pytest.mark.asyncio
async def test_open_circuit_fails_fast_without_second_discovery() -> None:
    provider = MCPToolProvider(failure_threshold=1, cooldown_seconds=30)
    client = FakeClient(error=ConnectionError("monitor unavailable"))

    async def factory():
        return client

    with pytest.raises(ConnectionError):
        await provider.get_tools(factory)
    with pytest.raises(MCPCircuitOpenError):
        await provider.get_tools(factory)

    assert client.calls == 1
    assert provider.state == "open"


@pytest.mark.asyncio
async def test_half_open_probe_recovers_after_cooldown() -> None:
    provider = MCPToolProvider(failure_threshold=1, cooldown_seconds=0)
    failing = FakeClient(error=ConnectionError("monitor unavailable"))
    healthy = FakeClient(tools=["memory"])
    clients = iter([failing, healthy])

    async def factory():
        return next(clients)

    with pytest.raises(ConnectionError):
        await provider.get_tools(factory)

    assert await provider.get_tools(factory) == ["memory"]
    assert provider.state == "closed"
