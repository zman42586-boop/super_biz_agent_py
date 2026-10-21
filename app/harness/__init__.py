"""Durable runtime exports, loaded lazily so the local collector stays lightweight."""

from importlib import import_module

_EXPORTS = {
    "HarnessRepository": "repository",
    "get_harness_repository": "repository",
    "HarnessRunContext": "runtime",
    "get_run_context": "runtime",
    "harness_run_context": "runtime",
    "HarnessSettings": "config",
    "harness_settings": "config",
    "LoopGuard": "loop_guard",
    "LoopGuardDecision": "loop_guard",
    "LoopGuardPolicy": "loop_guard",
    "ToolGateway": "tool_gateway",
    "ToolPolicy": "tool_gateway",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        return getattr(import_module(f"app.harness.{_EXPORTS[name]}"), name)
    raise AttributeError(name)
