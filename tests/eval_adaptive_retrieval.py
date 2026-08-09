"""第二轮自适应检索、拒答和延迟评测。"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from statistics import mean

from tests.eval_retrieval_v2 import _load_cases, _metrics

ROOT = Path(__file__).resolve().parents[1]
NO_ANSWER_QUERIES = [
    "公司私有 acme_solver.mexw64 报 E9472，内部协议握手失败",
    "MATLAB Online 与企业 OneDrive 同步冲突，云端文件出现 UNKNOWN_SYNC_77",
    "Robotics System Toolbox 的 ROS2 DDS 发现不到自定义域 143",
    "OPC UA 客户端证书轮换后 BadCertificateUriInvalid",
    "Database Toolbox 连接内部 JDBC 服务时 TLS_ALERT_1205",
    "Azure Blob SAS token 刷新后 MATLAB 返回 AUTH_SCOPE_X9",
    "Data Acquisition Toolbox 读取 NI PXI 时出现设备码 DAQMX_8842",
    "MATLAB Coder 为私有 DSP 内核生成代码时 INTERNAL_CG_451",
    "Stateflow 私有图模型报语义检查码 SF_SEMANTIC_702",
    "SimBiology 自定义动力学出现 NEGATIVE_STATE_CUSTOM_19"
]


def run() -> dict:
    from app.services.vector_store_manager import vector_store_manager

    answer_rows = []
    for case in _load_cases():
        started = time.perf_counter()
        outcome = vector_store_manager.search_with_diagnostics(case["query"], k=5)
        latency = (time.perf_counter() - started) * 1000
        answer_rows.append(
            {
                "id": case["id"],
                "confidence": outcome.confidence.score,
                "level": outcome.confidence.level,
                "attempts": outcome.attempts,
                "rewritten": outcome.rewritten,
                "reranker_used": outcome.reranker_used,
                "latency_ms": latency,
                **_metrics(case["relevant_docs"], outcome.documents, 5),
            }
        )

    no_answer_rows = []
    for index, query in enumerate(NO_ANSWER_QUERIES, 1):
        started = time.perf_counter()
        outcome = vector_store_manager.search_with_diagnostics(query, k=5)
        no_answer_rows.append(
            {
                "id": f"no_answer_{index}",
                "confidence": outcome.confidence.score,
                "level": outcome.confidence.level,
                "attempts": outcome.attempts,
                "rewritten": outcome.rewritten,
                "reranker_used": outcome.reranker_used,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "correct_abstention": outcome.confidence.level == "low",
            }
        )

    latencies = sorted(row["latency_ms"] for row in answer_rows + no_answer_rows)
    summary = {
        "answerable_count": len(answer_rows),
        "no_answer_count": len(no_answer_rows),
        "recall_at_5": mean(row["recall_at_5"] for row in answer_rows),
        "precision_at_5": mean(row["precision_at_5"] for row in answer_rows),
        "mrr": mean(row["mrr"] for row in answer_rows),
        "ndcg_at_5": mean(row["ndcg_at_5"] for row in answer_rows),
        "hit_at_1": mean(row["hit_at_1"] for row in answer_rows),
        "correct_abstention_rate": mean(row["correct_abstention"] for row in no_answer_rows),
        "answerable_low_confidence_rate": mean(row["level"] == "low" for row in answer_rows),
        "retry_rate": mean(row["attempts"] == 2 for row in answer_rows + no_answer_rows),
        "reranker_rate": mean(row["reranker_used"] for row in answer_rows + no_answer_rows),
        "latency_mean_ms": mean(latencies),
        "latency_p95_ms": latencies[math.ceil(len(latencies) * 0.95) - 1],
    }
    return {"summary": summary, "answerable": answer_rows, "no_answer": no_answer_rows}


if __name__ == "__main__":
    report = run()
    output = ROOT / "work" / "eval_adaptive_round2.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
