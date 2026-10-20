"""Safe parser/transport checks and injection design tests; no MATLAB is launched."""

import ast
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.run_matlab_case import CASES, run_case

ROOT = Path(__file__).resolve().parents[1]


def load_functions(path, names, namespace):
    nodes = [node for node in ast.parse(path.read_text(encoding="utf-8-sig")).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("text,kind", [
    ("Stack Overflow", "stack_overflow"), ("ACCESS VIOLATION", "access_violation"),
    ("Out of Memory", "out_of_memory"), ("ordinary exit", "unknown"),
])
def test_actual_dump_parser_classifies_case_insensitively_and_keeps_provenance(tmp_path, text, kind):
    namespace = load_functions(ROOT / "scripts/lhm_alert_agent.py", {"parse_crash_dump"},
                               {"os": os, "datetime": datetime, "UTC": UTC})
    path = tmp_path / "dump.txt"
    path.write_text(text + "\nStack Trace:\nf.m at line 42\n", encoding="utf-8")
    row = namespace["parse_crash_dump"](str(path))
    assert row["crash_type"] == kind
    assert row["stack_trace"] == "f.m at line 42"
    assert row["dump_source"] == "dump.txt" and len(row["dump_sha256"]) == 64


def test_monitor_tools_return_one_current_point_not_fabricated_history():
    namespace = load_functions(ROOT / "mcp_servers/monitor_server.py",
                               {"query_cpu_metrics", "query_memory_metrics"},
                               {"Dict": dict, "Any": object, "Optional": __import__("typing").Optional,
                                "datetime": datetime, "UTC": UTC,
                                "socket": SimpleNamespace(gethostname=lambda: "real-mcp-host"),
                                "psutil": SimpleNamespace(cpu_percent=lambda **kwargs: 42,
                                    cpu_count=lambda **kwargs: 8,
                                    virtual_memory=lambda: SimpleNamespace(percent=65, used=1024, total=4096)),
                                "mcp": SimpleNamespace(tool=lambda: lambda function: function),
                                "log_tool_call": lambda function: function})
    for name in ("query_cpu_metrics", "query_memory_metrics"):
        row = namespace[name]("arbitrary-label", start_time="2020-01-01 00:00:00", interval="0m")
        assert row["host_id"] == "real-mcp-host" and row["historical_available"] is False
        assert row["data_source"] == "current_snapshot" and len(row["data_points"]) == 1
        assert row["observed_at"] == row["data_points"][0]["timestamp"]


def test_fault_design_is_dry_run_and_does_not_claim_actual_observations(monkeypatch):
    process = Mock(side_effect=AssertionError("Preview must not launch MATLAB"))
    monkeypatch.setattr("scripts.run_matlab_case.subprocess.run", process)
    cases = json.loads((CASES / "safe_cases.json").read_text(encoding="utf-8"))["cases"]
    for case in cases:
        result = run_case(case["case_id"])
        assert result["execution_status"] == "not_executed" and result["actual_observation"] is None
        assert not result["knowledge_annotations"]
    with pytest.raises(ValueError):
        run_case("mex_crash", execute=True)
    process.assert_not_called()
