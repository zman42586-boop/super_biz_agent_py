"""AIOps Eval Runner — 跑全部 eval 场景，输出两套评分报告。

用法:
    .venv/Scripts/python.exe -m tests.eval_runner
    .venv/Scripts/python.exe -m tests.eval_runner --scenario 01   # 只跑一个
    make eval

评测维度:
  - RAG 检索: Recall@K / Precision@K / MRR（纯向量计算，不调 LLM）
  - LLM 输出: LLM-as-Judge（Accuracy/Evidence/Actionable 子维度打分）
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
import time
from pathlib import Path

from app.evaluation.experiment import build_experiment_report, compare_experiments
from app.models.alert import AlertEvidence, AlertRecord
from app.services.aiops_service import aiops_service
from tests.eval_judge import EvalScore, judge_report, load_scenario
from tests.eval_retrieval import compute_metrics

SCENARIOS_DIR = Path(__file__).resolve().parent / "eval_scenarios"


def build_alert_record(scenario: dict) -> AlertRecord:
    alert = scenario["alert"]
    evidence_raw = alert.get("evidence", {})
    evidence = AlertEvidence(
        lhm_url=evidence_raw.get("lhm_url"),
        recent_points=evidence_raw.get("recent_points", []),
        crash_log=evidence_raw.get("crash_log"),
        crash_type=evidence_raw.get("crash_type"),
        monitored_process=evidence_raw.get("monitored_process"),
        system_snapshot=evidence_raw.get("system_snapshot"),
        extra=evidence_raw.get("extra", {}),
    )
    return AlertRecord(
        alert_id=f"eval_{scenario['id']}",
        source="eval_harness",
        host=alert.get("host", "eval-host"),
        severity=alert.get("severity", "critical"),
        alert_name=alert.get("alert_name", ""),
        metric=alert.get("metric", "process_exit"),
        value=float(alert.get("value", 0)),
        threshold=float(alert.get("threshold", 1)),
        duration_sec=int(alert.get("duration_sec", 0)),
        sensor_id=alert.get("sensor_id", ""),
        ts=alert.get("ts", ""),
        evidence=evidence,
        status="active",
    )


async def run_one(scenario: dict) -> tuple[dict, str | None, EvalScore | None, dict | None]:
    """跑单个场景，返回 (scenario, report, judge_score, retrieval_metrics)。"""
    alert = build_alert_record(scenario)
    retrieval = compute_metrics(scenario, top_k=5)
    report = await aiops_service.execute_alert_diagnosis(alert)
    score = await judge_report(scenario, report)
    return scenario, report, score, retrieval


async def run_all(scenarios: list[dict]) -> list[tuple[dict, str | None, EvalScore | None, dict | None]]:
    results = []
    for i, sc in enumerate(scenarios, 1):
        print(f"  [{i}/{len(scenarios)}] {sc['name']}...", end=" ", flush=True)
        start = time.monotonic()
        try:
            _, report, score, retrieval = await run_one(sc)
            elapsed = time.monotonic() - start
            status = "PASS" if (score and score.score >= 7) else ("WARN" if (score and score.score >= 4) else "FAIL")
            r_info = ""
            if retrieval and not retrieval.get("skipped", True):
                r_info = f"R@{retrieval['recall_at_k']:.0%} MRR={retrieval['mrr']:.0%}"
            print(f"{status} {score.score}/10  {r_info}  ({elapsed:.0f}s)")
            results.append((sc, report, score, retrieval))
        except Exception as e:
            elapsed = time.monotonic() - start
            print(f"ERROR ({elapsed:.0f}s): {e}")
            results.append((sc, "", EvalScore(score=0, accuracy=0, evidence=0, actionable=0, comment=str(e)), None))
    return results


def print_report(
    results: list[tuple[dict, str | None, EvalScore | None, dict | None]],
    prev_judge_avg: float | None = None,
    prev_retrieval_avg: dict | None = None,
) -> tuple[float, dict]:
    """打印两套评分报告，返回 (judge_avg, {recall_avg, precision_avg, mrr_avg})。"""

    # ── LLM-as-Judge ──
    print()
    print("=" * 82)
    print("  AIOps Eval - LLM-as-Judge (诊断报告质量)")
    print("=" * 82)
    print(f"  {'场景':<28} {'总分':>4}  {'A':>1}/{'E':>1}/{'A':>1}  备注")
    print(f"  {'-'*28}  {'---':>4}  {'-'*7}  {'-'*24}")
    judge_total = 0
    judge_count = 0
    for sc, _, score, _ in results:
        if score is None:
            continue
        aea = f"{score.accuracy}/{score.evidence}/{score.actionable}"
        comment = score.comment[:42] if score.comment else ""
        print(f"  {sc['name']:<26}  {score.score:>3}/10  {aea:<7} {comment}")
        judge_total += score.score
        judge_count += 1
    judge_avg = judge_total / max(judge_count, 1)
    print(f"  {'-'*82}")
    print(f"  LLM-as-Judge 平均: {judge_avg:.1f}/10  "
          f"通过率(>=5): {sum(1 for _, _, s, _ in results if s and s.score >= 5)/max(judge_count,1)*100:.0f}%")
    if prev_judge_avg is not None:
        delta = judge_avg - prev_judge_avg
        sym = "+" if delta > 0 else ("-" if delta < 0 else "=")
        print(f"  上次: {prev_judge_avg:.1f}/10  {sym} {delta:+.1f}")
    print("=" * 82)

    # ── Retrieval Metrics ──
    print()
    print("=" * 82)
    print("  AIOps Eval - RAG 检索评测 (Recall@5 / Precision@5 / MRR)")
    print("=" * 82)
    print(f"  {'场景':<28} {'R@5':>5}  {'P@5':>5}  {'MRR':>5}  备注")
    print(f"  {'-'*28}  {'---':>5}  {'---':>5}  {'---':>5}  {'-'*20}")
    recall_sum = precision_sum = mrr_sum = 0.0
    retrieval_count = 0
    for sc, _, _, ret in results:
        if ret is None or ret.get("skipped", True):
            r = ret.get("message", "skipped") if ret else "skipped"
            print(f"  {sc['name']:<26}  {'N/A':>5}  {'N/A':>5}  {'N/A':>5}  {r}")
            continue
        rk = ret["recall_at_k"]
        pk = ret["precision_at_k"]
        mk = ret["mrr"]
        matched = f"{len([d for d in ret['retrieved_docs'] if any(d.lower().count(x.lower())>0 or x.lower() in d.lower() for x in ret['relevant_docs'])])}/{len(ret['relevant_docs'])} matched"
        print(f"  {sc['name']:<26}  {rk:>4.0%}  {pk:>4.0%}  {mk:>4.0%}  {matched[:20]}")
        recall_sum += rk
        precision_sum += pk
        mrr_sum += mk
        retrieval_count += 1

    retrieval_avg = {
        "recall_at_k": recall_sum / max(retrieval_count, 1),
        "precision_at_k": precision_sum / max(retrieval_count, 1),
        "mrr": mrr_sum / max(retrieval_count, 1),
    }
    print(f"  {'-'*82}")
    print(f"  Recall@5 平均:  {retrieval_avg['recall_at_k']:.0%}    "
          f"Precision@5 平均: {retrieval_avg['precision_at_k']:.0%}    "
          f"MRR 平均: {retrieval_avg['mrr']:.0%}")
    if prev_retrieval_avg:
        rd = retrieval_avg["recall_at_k"] - prev_retrieval_avg.get("recall_at_k", 0)
        print(f"  Recall 对比: {prev_retrieval_avg['recall_at_k']:.0%} -> "
              f"{retrieval_avg['recall_at_k']:.0%}  ({'+'if rd>=0 else ''}{rd:+.0%})")
    print("=" * 82)

    return judge_avg, retrieval_avg


# ── Score file (兼容旧格式 + 新增检索指标) ──

SCORE_FILE = Path(__file__).resolve().parent / ".last_eval_score"


def _save_score(judge_avg: float, retrieval_avg: dict) -> None:
    with open(SCORE_FILE, "w") as f:
        json.dump({
            "judge_avg": round(judge_avg, 1),
            "recall_at_k": round(retrieval_avg["recall_at_k"], 3),
            "precision_at_k": round(retrieval_avg["precision_at_k"], 3),
            "mrr": round(retrieval_avg["mrr"], 3),
        }, f)


def _load_last_score() -> tuple[float | None, dict | None]:
    if SCORE_FILE.exists():
        try:
            with open(SCORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data.get("judge_avg"), data
            return float(data), None  # 旧格式兼容
        except (ValueError, OSError, json.JSONDecodeError):
            pass
    return None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AIOps offline evaluation dataset")
    parser.add_argument("--scenario", help="Run one scenario id, for example 01")
    parser.add_argument("--experiment", default="local", help="Version/experiment name")
    parser.add_argument("--output", type=Path, help="Write a versioned JSON experiment report")
    parser.add_argument("--baseline", type=Path, help="Compare output with a previous report")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit with code 2 when the baseline release gate fails",
    )
    args = parser.parse_args()
    files = sorted(glob.glob(os.path.join(SCENARIOS_DIR, "scenario_*.json")))

    single_scenario = bool(args.scenario)
    if single_scenario:
        sid = args.scenario
        files = [f for f in files if f"scenario_{sid}" in f]
        if not files:
            print(f"未找到 scenario_{sid}")
            sys.exit(1)

    if not files:
        print("未找到 eval 场景文件 (tests/eval_scenarios/scenario_*.json)")
        sys.exit(1)

    print(f"\n加载 {len(files)} 个 eval 场景")
    scenarios = [load_scenario(f) for f in files]

    prev_judge_avg, prev_retrieval_avg = _load_last_score()

    print("开始评估...")
    results = asyncio.run(run_all(scenarios))

    judge_avg, retrieval_avg = print_report(
        results,
        prev_judge_avg=prev_judge_avg,
        prev_retrieval_avg=prev_retrieval_avg,
    )

    if not single_scenario:
        _save_score(judge_avg, retrieval_avg)

    cases = []
    for scenario, _report, score, retrieval in results:
        cases.append(
            {
                "id": scenario["id"],
                "name": scenario["name"],
                "judge_score": score.score if score else None,
                "judge": score.model_dump() if score else None,
                "retrieval": (
                    {
                        "recall_at_k": retrieval["recall_at_k"],
                        "precision_at_k": retrieval["precision_at_k"],
                        "mrr": retrieval["mrr"],
                    }
                    if retrieval and not retrieval.get("skipped", True)
                    else None
                ),
            }
        )
    experiment = build_experiment_report(args.experiment, cases)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"实验报告已写入: {args.output}")

    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        comparison = compare_experiments(baseline, experiment)
        print("\nRelease gate:")
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        if args.fail_on_regression and not comparison["passed"]:
            sys.exit(2)


if __name__ == "__main__":
    main()
