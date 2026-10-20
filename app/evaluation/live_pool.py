"""Read-only snapshot/pooling from the currently deployed Milvus collection.

Older indexes are mapped to content-versioned snapshot IDs without rebuilding
or changing the collection. The native-ID mapping is outside the corpus hash.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.pooling import build_pool, export_annotations, fingerprint
from app.services.document_identity import digest, source_key


def snapshot_rows(rows: list[dict]) -> tuple[dict, dict[str, str]]:
    documents = {}
    for row in rows:
        metadata = row["metadata"]
        source = source_key(metadata.get("_source") or metadata.get("_file_name") or "unknown")
        documents.setdefault(source, []).append(row)
    chunks, native_map = [], {}
    for source, records in sorted(documents.items()):
        parents = sorted({str(row["metadata"].get("_parent_content") or row["content"]) for row in records})
        document_text = "\n\n".join(parents)
        document_id, version = digest(source), digest(document_text)
        by_chunk = {}
        for row in records:
            metadata = row["metadata"]
            parent = str(metadata.get("_parent_content") or row["content"])
            parent_id = f"{document_id}@{version}:p:{digest(parent)}"
            chunk_id = f"{parent_id}:c:{digest(row['content'])}"
            native_map[str(row["id"])] = chunk_id
            by_chunk[chunk_id] = {
                "document_id": document_id, "document_version": version, "parent_id": parent_id,
                "chunk_id": chunk_id, "source": source, "title": metadata.get("_document_title", ""),
                "section": metadata.get("_header_path", ""), "text": row["content"],
                "parent_text": parent, "document_text": document_text,
            }
        chunks.extend(by_chunk[key] for key in sorted(by_chunk))
    return {"schema_version": 1, "id_scheme": "deployed-content-snapshot-v1",
            "corpus_hash": fingerprint(chunks), "chunks": chunks}, native_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("Use a new output directory to preserve the snapshot")
    from app.core.milvus_client import milvus_manager
    from app.services.vector_store_manager import vector_store_manager

    collection = milvus_manager.get_collection()
    if collection.num_entities >= 16000:
        raise ValueError("Use a paginated export for larger collections; refusing to truncate")
    rows = collection.query(expr='id != ""', output_fields=["id", "content", "metadata"], limit=16000)
    corpus, native_map = snapshot_rows(rows)
    queries = json.loads(args.queries.read_text(encoding="utf-8"))
    runs = {"dense": {}, "milvus_bm25": {}}
    for index, query in enumerate(queries, 1):
        print(f"[{index}/{len(queries)}] {query['id']}", flush=True)
        for route, field in (("dense", "dense"), ("milvus_bm25", "sparse")):
            hits = vector_store_manager._single_field_search(query["text"], field=field, limit=args.top_n, expr=None)
            runs[route][query["id"]] = [{"chunk_id": native_map[doc.metadata["_milvus_id"]], "score": score}
                                       for doc, score in hits]
    pool = build_pool(corpus, queries, top_n=args.top_n, dense_runs=runs)
    args.output_dir.mkdir(parents=True)
    for name, value in (("corpus.json", corpus), ("native-id-map.json", native_map), ("pool.json", pool)):
        (args.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, ranking in runs.items():
        (args.output_dir / f"{name}.json").write_text(json.dumps({"corpus_hash": corpus["corpus_hash"], "rankings": ranking},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    export_annotations(pool, args.output_dir / "annotations.csv")
    print(json.dumps({"native_rows": len(rows), "unique_chunks": len(corpus["chunks"]),
                      "queries": len(queries), "candidates": sum(len(row["candidates"]) for row in pool["queries"]),
                      "pool_hash": pool["pool_hash"], "grades": "unjudged"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
