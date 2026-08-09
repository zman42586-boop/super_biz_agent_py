"""使用本地 BGE 模型运行检索基线与优化方案对照评测。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from langchain_core.documents import Document

from app.services.document_splitter_service import document_splitter_service
from app.services.retrieval_reranker import expand_query, rerank_candidates
from app.services.vector_embedding_service import vector_embedding_service
from tests.eval_retrieval import build_eval_query, calculate_metrics

ROOT = Path(__file__).resolve().parents[1]


def _legacy_query(scenario: dict[str, Any]) -> str:
    alert = scenario.get("alert", {})
    evidence = alert.get("evidence", {})
    return " ".join(
        str(part)
        for part in (
            alert.get("alert_name", ""),
            scenario.get("description", ""),
            alert.get("metric", ""),
            evidence.get("crash_type", ""),
        )
        if part
    )


def _load_chunks() -> list[Document]:
    chunks: list[Document] = []
    for path in sorted((ROOT / "aiops-docs").glob("*.md")):
        chunks.extend(
            document_splitter_service.split_document(
                path.read_text(encoding="utf-8"), str(path)
            )
        )
    return chunks


def _load_scenarios() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "tests" / "eval_scenarios").glob("*.json"))
    ]


def _cosine_scores(query: str, chunk_matrix: np.ndarray) -> np.ndarray:
    query_vector = np.asarray(
        vector_embedding_service.embed_query(query), dtype=np.float32
    )
    return chunk_matrix @ query_vector


def _dense_document_search(
    query: str, chunks: list[Document], chunk_matrix: np.ndarray, k: int
) -> list[Document]:
    scores = _cosine_scores(query, chunk_matrix)
    best_by_source: dict[str, tuple[Document, float]] = {}
    for doc, score in zip(chunks, scores, strict=True):
        source = str(doc.metadata.get("_file_name") or doc.metadata.get("_source"))
        if source not in best_by_source or score > best_by_source[source][1]:
            best_by_source[source] = (doc, float(score))
    return [
        doc
        for doc, _ in sorted(
            best_by_source.values(), key=lambda item: item[1], reverse=True
        )[:k]
    ]


def _optimized_search(
    query: str, chunks: list[Document], chunk_matrix: np.ndarray, k: int
) -> list[Document]:
    scores = _cosine_scores(expand_query(query), chunk_matrix)
    candidate_count = min(len(chunks), max(k * 6, k))
    indexes = np.argsort(scores)[::-1][:candidate_count]
    candidates = [(chunks[index], float(scores[index])) for index in indexes]
    return rerank_candidates(
        query,
        candidates,
        k=k,
        dense_weight=0.75,
        score_higher_is_better=True,
    )


def run(top_k: int = 5) -> dict[str, Any]:
    chunks = _load_chunks()
    chunk_matrix = np.asarray(
        vector_embedding_service.embed_documents([doc.page_content for doc in chunks]),
        dtype=np.float32,
    )
    scenarios = _load_scenarios()
    modes: dict[str, list[dict[str, Any]]] = {"baseline": [], "optimized": []}

    for scenario in scenarios:
        queries = {
            "baseline": _legacy_query(scenario),
            "optimized": build_eval_query(scenario),
        }
        searches = {
            "baseline": _dense_document_search,
            "optimized": _optimized_search,
        }
        for mode in modes:
            docs = searches[mode](queries[mode], chunks, chunk_matrix, top_k)
            metrics = calculate_metrics(
                scenario.get("relevant_docs", []),
                [str(doc.metadata.get("_file_name", "")) for doc in docs],
                top_k,
            )
            modes[mode].append(
                {
                    "id": scenario["id"],
                    "query": queries[mode],
                    **metrics,
                }
            )

    averages = {}
    for mode, rows in modes.items():
        averages[mode] = {
            metric: float(np.mean([row[metric] for row in rows]))
            for metric in ("recall_at_k", "precision_at_k", "mrr")
        }
    return {
        "top_k": top_k,
        "document_count": len({doc.metadata.get("_file_name") for doc in chunks}),
        "chunk_count": len(chunks),
        "scenario_count": len(scenarios),
        "averages": averages,
        "results": modes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.top_k)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
