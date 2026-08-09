"""40 条隔离查询的三阶段真实 Milvus 检索评测。"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from statistics import mean

from langchain_core.documents import Document

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.retrieval_reranker import expand_query, rerank_documents, score_documents
from app.services.vector_embedding_service import vector_embedding_service
from app.services.vector_store_manager import vector_store_manager

ROOT = Path(__file__).resolve().parents[1]


def _load_cases() -> list[dict]:
    groups = json.loads((ROOT / "tests" / "eval_cases_v2.json").read_text(encoding="utf-8"))
    return [
        {"id": f"{group['category']}_{index + 1}", "query": query["text"], "relevant_docs": query["relevant_docs"]}
        for group in groups
        for index, query in enumerate(group["queries"])
    ]


def _name(doc: Document) -> str:
    return str(doc.metadata.get("_file_name") or doc.metadata.get("_source") or "")


def _dedupe(docs: list[Document], k: int) -> list[Document]:
    result: list[Document] = []
    seen: set[str] = set()
    for doc in docs:
        key = _name(doc).lower()
        if key and key not in seen:
            seen.add(key)
            result.append(doc)
        if len(result) == k:
            break
    return result


def _metrics(relevant: list[str], docs: list[Document], k: int) -> dict[str, float]:
    names = [_name(doc).lower() for doc in docs[:k]]
    relevant_lower = [name.lower() for name in relevant]
    relevance = [1 if any(rel in name for rel in relevant_lower) else 0 for name in names]
    matched = sum(any(rel in name for name in names) for rel in relevant_lower)
    first = next((index for index, value in enumerate(relevance, 1) if value), None)
    dcg = sum(value / math.log2(index + 1) for index, value in enumerate(relevance, 1))
    ideal_count = min(len(relevant), k)
    idcg = sum(1 / math.log2(index + 1) for index in range(1, ideal_count + 1))
    return {
        "recall_at_5": matched / len(relevant_lower),
        "precision_at_5": sum(relevance) / k,
        "mrr": 1 / first if first else 0.0,
        "ndcg_at_5": dcg / idcg if idcg else 0.0,
        "hit_at_1": float(bool(relevance and relevance[0])),
    }


def _dense(query: str, candidate_k: int = 40) -> list[Document]:
    collection = milvus_manager.get_collection()
    rows = collection.search(
        data=[vector_embedding_service.embed_query(query)],
        anns_field="dense",
        param={"metric_type": "IP", "params": {}},
        limit=candidate_k,
        output_fields=["content", "metadata"],
    )[0]
    return [Document(page_content=hit.entity.get("content"), metadata=hit.entity.get("metadata")) for hit in rows]


def _hybrid(query: str, candidate_k: int = 20) -> list[tuple[Document, float]]:
    rows = vector_store_manager.vector_store.similarity_search_with_score(
        expand_query(query),
        k=candidate_k,
        fetch_k=max(config.rag_dense_candidates, config.rag_sparse_candidates),
        ranker_type="rrf",
        ranker_params={"k": config.rag_rrf_k},
    )
    return rows


def run(top_k: int = 5) -> dict:
    cases = _load_cases()
    weights = (0.2, 0.35, 0.5, 0.7, 1.0)
    modes: dict[str, list[dict]] = {"dense_raw": [], "dense_expanded": [], "hybrid_expanded": []}
    modes.update({f"hybrid_cross_w{int(weight * 100)}": [] for weight in weights})
    for case in cases:
        started = time.perf_counter()
        dense_docs = _dedupe(_dense(case["query"]), top_k)
        dense_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        dense_expanded_docs = _dedupe(_dense(expand_query(case["query"])), top_k)
        dense_expanded_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        hybrid_rows = _hybrid(case["query"])
        hybrid_candidates = [doc for doc, _ in hybrid_rows]
        hybrid_scores = [float(score) for _, score in hybrid_rows]
        hybrid_docs = _dedupe(hybrid_candidates, top_k)
        hybrid_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        cross_scores = score_documents(case["query"], hybrid_candidates)
        rerank_ms = (time.perf_counter() - started) * 1000

        for mode, docs, latency in (
            ("dense_raw", dense_docs, dense_ms),
            ("dense_expanded", dense_expanded_docs, dense_expanded_ms),
            ("hybrid_expanded", hybrid_docs, hybrid_ms),
        ):
            modes[mode].append(
                {"id": case["id"], "retrieved": [_name(doc) for doc in docs], "latency_ms": latency, **_metrics(case["relevant_docs"], docs, top_k)}
            )
        for weight in weights:
            docs = rerank_documents(
                case["query"], hybrid_candidates, top_k,
                reranker_weight=weight, cross_scores=cross_scores,
                initial_scores=hybrid_scores,
            )
            modes[f"hybrid_cross_w{int(weight * 100)}"].append(
                {"id": case["id"], "retrieved": [_name(doc) for doc in docs], "latency_ms": hybrid_ms + rerank_ms, **_metrics(case["relevant_docs"], docs, top_k)}
            )

    summary = {}
    for mode, rows in modes.items():
        latencies = sorted(row["latency_ms"] for row in rows)
        summary[mode] = {
            key: mean(row[key] for row in rows)
            for key in ("recall_at_5", "precision_at_5", "mrr", "ndcg_at_5", "hit_at_1")
        }
        summary[mode]["latency_mean_ms"] = mean(latencies)
        summary[mode]["latency_p95_ms"] = latencies[math.ceil(len(latencies) * 0.95) - 1]
    split_summary = {}
    for split_name, suffixes in {"development": ("_1", "_2", "_3"), "held_out": ("_4", "_5")}.items():
        split_summary[split_name] = {}
        for mode, rows in modes.items():
            selected = [row for row in rows if row["id"].endswith(suffixes)]
            split_summary[split_name][mode] = {
                key: mean(row[key] for row in selected)
                for key in ("recall_at_5", "precision_at_5", "mrr", "ndcg_at_5", "hit_at_1")
            }
    return {"case_count": len(cases), "top_k": top_k, "summary": summary, "split_summary": split_summary, "results": modes}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "work" / "eval_retrieval_v2.json")
    args = parser.parse_args()
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(report["split_summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
