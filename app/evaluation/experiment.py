"""Versioned offline evaluation reports and deterministic regression gates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def build_experiment_report(
    experiment_name: str,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    judged = [row for row in cases if row.get("judge_score") is not None]
    retrieved = [row for row in cases if row.get("retrieval")]

    def average(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "schema_version": 1,
        "experiment_name": experiment_name,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scenario_count": len(cases),
        "aggregate": {
            "judge_average": average([float(row["judge_score"]) for row in judged]),
            "judge_pass_rate": average(
                [1.0 if float(row["judge_score"]) >= 5 else 0.0 for row in judged]
            ),
            "recall_at_k": average(
                [float(row["retrieval"]["recall_at_k"]) for row in retrieved]
            ),
            "precision_at_k": average(
                [float(row["retrieval"]["precision_at_k"]) for row in retrieved]
            ),
            "mrr": average([float(row["retrieval"]["mrr"]) for row in retrieved]),
        },
        "cases": cases,
    }


def compare_experiments(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    max_judge_drop: float = 0.0,
    max_recall_drop: float = 0.02,
    max_mrr_drop: float = 0.02,
    max_case_judge_drop: float = 1.0,
) -> dict[str, Any]:
    """Compare the same dataset and return an explainable release-gate decision."""
    if baseline.get("scenario_count") != candidate.get("scenario_count"):
        return {
            "passed": False,
            "reasons": ["scenario_count_changed"],
            "deltas": {},
            "regressed_cases": [],
        }

    baseline_cases = {str(row["id"]): row for row in baseline.get("cases", [])}
    candidate_cases = {str(row["id"]): row for row in candidate.get("cases", [])}
    if set(baseline_cases) != set(candidate_cases):
        return {
            "passed": False,
            "reasons": ["scenario_ids_changed"],
            "deltas": {},
            "regressed_cases": [],
        }

    baseline_aggregate = baseline.get("aggregate", {})
    candidate_aggregate = candidate.get("aggregate", {})

    def delta(name: str) -> float | None:
        before = baseline_aggregate.get(name)
        after = candidate_aggregate.get(name)
        if before is None or after is None:
            return None
        return round(float(after) - float(before), 4)

    deltas = {
        name: delta(name)
        for name in ("judge_average", "judge_pass_rate", "recall_at_k", "precision_at_k", "mrr")
    }
    reasons: list[str] = []
    thresholds = {
        "judge_average": max_judge_drop,
        "recall_at_k": max_recall_drop,
        "mrr": max_mrr_drop,
    }
    for metric, allowed_drop in thresholds.items():
        metric_delta = deltas[metric]
        if metric_delta is not None and metric_delta < -allowed_drop:
            reasons.append(f"{metric}_regressed")

    regressed_cases = []
    for case_id, before in baseline_cases.items():
        after = candidate_cases[case_id]
        before_score = before.get("judge_score")
        after_score = after.get("judge_score")
        if before_score is None or after_score is None:
            continue
        score_delta = round(float(after_score) - float(before_score), 4)
        if score_delta < -max_case_judge_drop:
            regressed_cases.append(
                {"id": case_id, "before": before_score, "after": after_score, "delta": score_delta}
            )
    if regressed_cases:
        reasons.append("case_level_regression")

    return {
        "passed": not reasons,
        "baseline": baseline.get("experiment_name"),
        "candidate": candidate.get("experiment_name"),
        "reasons": reasons,
        "deltas": deltas,
        "regressed_cases": regressed_cases,
        "thresholds": {
            "max_judge_drop": max_judge_drop,
            "max_recall_drop": max_recall_drop,
            "max_mrr_drop": max_mrr_drop,
            "max_case_judge_drop": max_case_judge_drop,
        },
    }
