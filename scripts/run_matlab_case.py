"""Design preview by default; explicitly execute one allowlisted isolated MATLAB case."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.services.document_identity import digest

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "fault_injection"


def run_case(case_id: str, *, execute: bool = False, matlab: str = "matlab") -> dict:
    manifest = json.loads((CASES / "safe_cases.json").read_text(encoding="utf-8"))
    case = next((row for row in manifest["cases"] if row["case_id"] == case_id), None)
    if case is None:
        raise ValueError("Unknown or disabled case; real OOM, MEX crashes and host failures are not executable")
    if not execute:
        return {**case, "execution_status": "not_executed", "dataset_version": manifest["version"]}
    script = (CASES / case["script"]).resolve()
    if script.parent != CASES.resolve():
        raise ValueError("Script is outside the allowlist directory")
    quoted = script.as_posix().replace("'", "''")
    # MATLAB catches language errors so that they are distinguished from an OS
    # process crash. Only the child created by this runner can be timed out.
    expression = (
        "try; run('" + quoted + "'); disp('AIOPS_CASE_NO_EXCEPTION'); "
        "catch exception; disp('AIOPS_CASE_ERROR'); "
        "disp(jsonencode(struct('identifier',exception.identifier,'message',exception.message,"
        "'stack',exception.stack))); end;"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="aiops_matlab_case_") as directory:
            result = subprocess.run([matlab, "-batch", expression], cwd=directory,
                                    capture_output=True, text=True, errors="replace",
                                    timeout=case["max_wall_seconds"], shell=False)
        log = result.stdout + "\n" + result.stderr
        kind = "matlab_error" if "AIOPS_CASE_ERROR" in log else "no_error" if result.returncode == 0 else "process_exit_unclassified"
        observation = {"kind": kind, "exit_code": result.returncode, "log": log}
    except subprocess.TimeoutExpired as exc:
        observation = {"kind": "runner_timeout", "note": "Only the test child was stopped; this does not prove a MATLAB crash",
                       "log": str(exc.stdout or "") + str(exc.stderr or "")}
    identifier = "fixture_" + digest(json.dumps(observation, sort_keys=True))[:24]
    return {**case, "execution_status": "observed_requires_review", "dataset_version": manifest["version"],
            "observed_at": datetime.now(UTC).isoformat(), "actual_observation": observation,
            "logs": [{"evidence_id": identifier, "text": observation["log"]}], "evidence_ids": [identifier],
            "limitations": "No host metric sampler or binary dump collector is attached; knowledge labels remain unjudged"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--matlab", default="matlab")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_case(args.case_id, execute=args.execute, matlab=args.matlab)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
