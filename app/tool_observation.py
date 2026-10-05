"""Tool observation policy shared by the gateway and progress guard."""

DYNAMIC_TOOL_NAMES = frozenset(
    {
        "get_current_time",
        "search_log",
        "query_cpu_metrics",
        "query_memory_metrics",
        "list_lhm_sensors",
        "get_lhm_temperature",
    }
)
