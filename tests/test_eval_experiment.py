from __future__ import annotations

from app.evaluation.experiment import build_experiment_report, compare_experiments


def _case(case_id: str, score: float, recall: float, mrr: float) -> dict:
    return {
        "id": case_id,
        "name": case_id,
        "judge_score": score,
        "judge": {"score": score},
        "retrieval": {
            "recall_at_k": recall,
            "precision_at_k": 0.5,
            "mrr": mrr,
        },
    }


def test_experiment_report_and_passing_comparison() -> None:
    baseline = build_experiment_report("baseline", [_case("a", 7, 0.8, 0.7)])
    candidate = build_experiment_report("candidate", [_case("a", 8, 0.9, 0.8)])

    comparison = compare_experiments(baseline, candidate)

    assert baseline["aggregate"]["judge_average"] == 7.0
    assert comparison["passed"] is True
    assert comparison["deltas"]["judge_average"] == 1.0


def test_comparison_explains_aggregate_and_case_regressions() -> None:
    baseline = build_experiment_report(
        "baseline", [_case("a", 8, 0.9, 0.9), _case("b", 8, 0.9, 0.9)]
    )
    candidate = build_experiment_report(
        "candidate", [_case("a", 6, 0.7, 0.7), _case("b", 8, 0.9, 0.9)]
    )

    comparison = compare_experiments(baseline, candidate)

    assert comparison["passed"] is False
    assert "judge_average_regressed" in comparison["reasons"]
    assert "recall_at_k_regressed" in comparison["reasons"]
    assert "case_level_regression" in comparison["reasons"]
    assert comparison["regressed_cases"][0]["id"] == "a"


def test_comparison_rejects_changed_dataset() -> None:
    baseline = build_experiment_report("baseline", [_case("a", 8, 0.9, 0.9)])
    candidate = build_experiment_report("candidate", [_case("b", 8, 0.9, 0.9)])
    comparison = compare_experiments(baseline, candidate)
    assert comparison == {
        "passed": False,
        "reasons": ["scenario_ids_changed"],
        "deltas": {},
        "regressed_cases": [],
    }
