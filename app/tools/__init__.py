"""工具模块 - 供 Agent 调用的各种工具"""


def __getattr__(name):
    from importlib import import_module

    modules = {
        "retrieve_knowledge": "knowledge_tool",
        "search_log": "log_tool",
        "get_current_time": "time_tool",
    }
    if name in modules:
        return getattr(import_module(f"app.tools.{modules[name]}"), name)
    raise AttributeError(name)


__all__ = [
    "retrieve_knowledge",
    "search_log",
    "get_current_time",
]
