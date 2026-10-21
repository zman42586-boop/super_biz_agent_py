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
                               {"os": os, "datetime": datetime, "UTC": UTC, "hashlib": __import__("hashlib")})
    path = tmp_path / "dump.txt"
    path.write_text(text + "\nStack Trace:\nf.m at line 42\n", encoding="utf-8")
    row = namespace["parse_crash_dump"](str(path))
    assert row["crash_type"] == kind
    assert row["stack_trace"] == "f.m at line 42"
    assert row["dump_source"] == "dump.txt" and len(row["dump_sha256"]) == 64


def test_monitor_tools_delegate_to_prometheus_without_sampling_mcp_host():
    expected = {"host_id": "pc", "data_source": "prometheus", "data_points": []}
    client = Mock()
    client.query.return_value = expected
    namespace = load_functions(ROOT / "mcp_servers/monitor_server.py",
                               {"query_metrics"}, {"config": object(),
                               "PrometheusClient": SimpleNamespace(from_settings=lambda _: client)})
    assert namespace["query_metrics"]("pc", "cpu_percent") == expected
    client.query.assert_called_once_with("pc", "cpu_percent", start_time=None, end_time=None, step_seconds=15)
    client.query.side_effect = RuntimeError("Prometheus unavailable")
    assert namespace["query_metrics"]("pc", "cpu_percent")["status"] == "error"


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
