"""Offline multi-route pools, human annotations and immutable dataset versions.

No production retrieval settings are changed. Dense rankings may be imported
from any retriever or computed with the project's embedding model by the CLI.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.document_identity import digest


def fingerprint(value: Any) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_:.+-]+|[\u4e00-\u9fff]+", text.lower())
    return [token for word in words for token in (
        [word] if not re.fullmatch(r"[\u4e00-\u9fff]{2,}", word)
        else [word[i:i + 2] for i in range(len(word) - 1)]
    )]


def validate_corpus(corpus: dict) -> None:
    chunks = corpus["chunks"]
    if corpus.get("corpus_hash") != fingerprint(chunks):
        raise ValueError("Corpus hash mismatch")
    for key in ("chunk_id",):
        if len({row[key] for row in chunks}) != len(chunks):
            raise ValueError("Duplicate chunk IDs")
    parents: dict[str, tuple] = {}
    for row in chunks:
        value = (row["document_id"], row["document_version"], row["parent_text"])
        if row["parent_id"] in parents and parents[row["parent_id"]] != value:
            raise ValueError("Parent ID maps to different content/versions")
        parents[row["parent_id"]] = value


def lexical_routes(query: str, chunks: list[dict], top_n: int) -> dict[str, list[tuple[str, float]]]:
    query_terms = set(tokens(query))
    counts = [Counter(tokens(row["text"])) for row in chunks]
    df = Counter(term for count in counts for term in count)
    average_length = sum(sum(count.values()) for count in counts) / max(len(counts), 1)
    bm25, exact, metadata = [], [], []
    # Preserve complete error codes/function names as an independent route.
    identifiers = set(re.findall(r"[A-Za-z0-9_:.+-]{3,}", query.lower()))
    for row, count in zip(chunks, counts, strict=True):
        length = sum(count.values())
        score = 0.0
        for term in query_terms:
            frequency = count[term]
            idf = math.log(1 + (len(chunks) - df[term] + 0.5) / (df[term] + 0.5))
            denominator = frequency + 1.2 * (0.25 + 0.75 * length / max(average_length, 1))
            score += idf * frequency * 2.2 / denominator
        if score > 0:
            bm25.append((row["chunk_id"], score))
        exact_score = sum(identifier in row["text"].lower() for identifier in identifiers)
        if exact_score:
            exact.append((row["chunk_id"], float(exact_score)))
        metadata_score = len(query_terms & set(tokens(row.get("title", "") + " " + row.get("section", "") + " " + row["source"])))
        if metadata_score:
            metadata.append((row["chunk_id"], float(metadata_score)))
    return {
        name: sorted(rows, key=lambda item: (-item[1], item[0]))[:top_n]
        for name, rows in (("bm25", bm25), ("exact", exact), ("metadata", metadata))
    }


def build_pool(corpus: dict, queries: list[dict], *, top_n: int = 20,
               dense_runs: dict[str, dict[str, list]] | None = None,
               granularity: str = "parent") -> dict:
    validate_corpus(corpus)
    if top_n < 1 or granularity not in {"parent", "chunk", "document"}:
        raise ValueError("Invalid top_n or granularity")
    if len({row["id"] for row in queries}) != len(queries):
        raise ValueError("Duplicate query IDs")
    by_id = {row["chunk_id"]: row for row in corpus["chunks"]}
    pooled = []
    for query in queries:
        routes = lexical_routes(query["text"], corpus["chunks"], top_n)
        for route, run in (dense_runs or {}).items():
            if route in routes:
                raise ValueError("Imported route would overwrite a pool route")
            routes[route] = [(item["chunk_id"], item.get("score")) for item in run.get(query["id"], [])[:top_n]]
        candidates = {}
        for route, ranked in routes.items():
            for rank, (chunk_id, score) in enumerate(ranked, 1):
                if chunk_id not in by_id:
                    raise ValueError(f"Ranking references unknown chunk: {chunk_id}")
                chunk = by_id[chunk_id]
                unit_id = chunk[f"{granularity}_id"]
                candidate = candidates.setdefault(unit_id, {
                    "unit_id": unit_id, "document_id": chunk["document_id"],
                    "document_version": chunk["document_version"], "source": chunk["source"],
                    "text": chunk[{"parent": "parent_text", "chunk": "text", "document": "document_text"}[granularity]],
                    "matches": [], "grade": None,
                })
                candidate["matches"].append({
                    "route": route, "rank": rank, "score": score,
                    "chunk_id": chunk_id, "parent_id": chunk["parent_id"],
                })
        pooled.append({"query": query, "candidates": list(candidates.values())})
    pool = {"schema_version": 1, "corpus_hash": corpus["corpus_hash"],
            "granularity": granularity, "top_n": top_n, "queries": pooled,
            "routes": ["bm25", "exact", "metadata", *(dense_runs or {})],
            "dense_included": any("dense" in route.lower() for route in (dense_runs or {}))}
    return {**pool, "pool_hash": fingerprint(pool)}


def export_annotations(pool: dict, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "pool_hash", "query_id", "query", "unit_id", "source", "text",
            "grade", "reviewer", "reason",
        ])
        writer.writeheader()
        for row in pool["queries"]:
            for candidate in row["candidates"]:
                writer.writerow({"pool_hash": pool["pool_hash"], "query_id": row["query"]["id"],
                                 "query": row["query"]["text"], "unit_id": candidate["unit_id"],
                                 "source": candidate["source"], "text": candidate["text"],
                                 "grade": "", "reviewer": "", "reason": ""})


def freeze_dataset(pool: dict, annotation_path: Path, *, version: str, output: Path,
                   threshold: int = 1) -> dict:
    if not version.strip() or threshold not in (1, 2):
        raise ValueError("Version and relevance threshold are required")
    body = {key: value for key, value in pool.items() if key != "pool_hash"}
    if fingerprint(body) != pool["pool_hash"]:
        raise ValueError("Pool changed after export")
    allowed = {(row["query"]["id"], candidate["unit_id"])
               for row in pool["queries"] for candidate in row["candidates"]}
    annotations, seen = [], set()
    with annotation_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            key = (row["query_id"], row["unit_id"])
            if row["pool_hash"] != pool["pool_hash"] or key not in allowed or key in seen:
                raise ValueError("Stale, unknown or duplicate annotation")
            seen.add(key)
            if row["grade"].strip() == "":
                continue
            if row["grade"].strip() not in {"0", "1", "2"} or not row["reviewer"].strip() or not row["reason"].strip():
                raise ValueError("Each grade requires 0/1/2, reviewer and reason")
            annotations.append({"query_id": key[0], "unit_id": key[1], "grade": int(row["grade"]),
                                "reviewer": row["reviewer"], "reason": row["reason"]})
    dataset = {"schema_version": 1, "version": version, "corpus_hash": pool["corpus_hash"],
               "pool_hash": pool["pool_hash"], "granularity": pool["granularity"],
               "relevance_threshold": threshold, "recall_scope": "judged_pool_approximation",
               "created_at": datetime.now(UTC).isoformat(), "annotations": annotations,
               "queries": [row["query"] for row in pool["queries"]]}
    dataset["dataset_hash"] = fingerprint(dataset)
    # Exclusive creation prevents silently changing an already published version.
    with output.open("x", encoding="utf-8") as stream:
        json.dump(dataset, stream, ensure_ascii=False, indent=2)
    return dataset


def evaluate_dataset(dataset: dict, run: dict, *, k: int = 5) -> dict:
    from app.evaluation.retrieval import METRIC_NAMES, ranking_metrics

    if dataset["dataset_hash"] != fingerprint({key: value for key, value in dataset.items() if key != "dataset_hash"}):
        raise ValueError("Frozen dataset hash mismatch")
    if run.get("corpus_hash") != dataset["corpus_hash"] or run.get("granularity") != dataset["granularity"]:
        raise ValueError("Run and judgments must use the same corpus and granularity")
    query_ids = {row["id"] for row in dataset["queries"]}
    if set(run["rankings"]) != query_ids:
        raise ValueError("Run must contain every frozen query exactly once (empty lists allowed)")
    rows = []
    for query in dataset["queries"]:
        judgments = {row["unit_id"]: row["grade"] for row in dataset["annotations"] if row["query_id"] == query["id"]}
        rows.append({"id": query["id"], **ranking_metrics(judgments, run["rankings"][query["id"]], k, dataset["relevance_threshold"])})
    ready = all(row["status"] != "needs_judgment" for row in rows)
    summary = {}
    for metric in METRIC_NAMES:
        values = [row[metric] for row in rows if row[metric] is not None]
        summary[metric] = sum(values) / len(values) if ready and values else None
    return {"dataset_hash": dataset["dataset_hash"], "corpus_hash": dataset["corpus_hash"],
            "granularity": dataset["granularity"], "k": k, "comparable": ready,
            "summary": summary, "results": rows}
