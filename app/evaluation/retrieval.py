"""Judgment-aware ranking metrics. Unjudged results are never negative labels."""

from __future__ import annotations

import math
from typing import Any

METRIC_NAMES = ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k", "hit_at_1")


def ranking_metrics(
    judgments: dict[str, int], retrieved: list[str], k: int = 5, threshold: int = 1
) -> dict[str, Any]:
    if k < 1 or threshold not in (1, 2):
        raise ValueError("k must be positive; relevance threshold must be 1 or 2")
    if any(type(grade) is not int or grade not in (0, 1, 2) for grade in judgments.values()):
        raise ValueError("Judgments must be integer grades 0, 1 or 2")
    ranked = list(dict.fromkeys(retrieved))[:k]
    unknown = [key for key in ranked if key not in judgments]
    result = {
        "k": k, "threshold": threshold, "retrieved": ranked,
        "unjudged_ids": unknown,
        "judged_at_k": len(ranked) - len(unknown),
        "judged_coverage": (len(ranked) - len(unknown)) / len(ranked) if ranked else 1.0,
        "recall_scope": "judged_pool_approximation",
        "mrr_depth": k,
    }
    # Refuse to compare an incompletely judged ranking. The annotation CLI can
    # extend the pool for a new retriever before evaluation is repeated.
    if unknown or not judgments:
        return {**result, "status": "needs_judgment", **dict.fromkeys(METRIC_NAMES)}
    relevant = {key for key, grade in judgments.items() if grade >= threshold}
    hits = [int(key in relevant) for key in ranked]
    first = next((rank for rank, hit in enumerate(hits, 1) if hit), None)
    dcg = sum((2 ** judgments[key] - 1) / math.log2(rank + 1) for rank, key in enumerate(ranked, 1))
    ideal = sorted(judgments.values(), reverse=True)[:k]
    idcg = sum((2 ** grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1))
    return {
        **result, "status": "ok" if relevant else "no_positive_labels",
        "recall_at_k": sum(hits) / len(relevant) if relevant else None,
        "precision_at_k": sum(hits) / k,
        "mrr": 1 / first if first else (0.0 if relevant else None),
        "ndcg_at_k": dcg / idcg if idcg else None,
        "hit_at_1": float(bool(hits and hits[0])),
    }


def legacy_file_metrics(relevant: list[str], retrieved: list[str], k: int) -> dict[str, Any]:
    """Explicit legacy document-level adapter; exact stem matching only.

    Legacy labels are positive-only and incomplete. This adapter preserves the
    old closed-world convention solely for regression; new pooled evaluation
    must use ranking_metrics with reviewed qrels.
    """
    def key(name: str) -> str:
        return name.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".md")

    ranked = list(dict.fromkeys(key(name) for name in retrieved))[:k]
    qrels = {key(name): 2 for name in relevant}
    qrels.update({name: 0 for name in ranked if name not in qrels})
    row = ranking_metrics(qrels, ranked, k)
    row["label_policy"] = "legacy_positive_only_closed_world"
    row["granularity"] = "document"
    return row
