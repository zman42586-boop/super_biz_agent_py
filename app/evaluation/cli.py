"""Offline evaluation commands; model/database dependencies are loaded on demand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.pooling import (
    build_pool, evaluate_dataset, export_annotations, fingerprint, freeze_dataset,
)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def snapshot(paths: list[Path]) -> dict:
    from app.services.document_splitter_service import document_splitter_service
    from app.services.document_preprocessing import preprocess_content

    chunks = []
    for path in sorted({file.resolve() for directory in paths for file in directory.rglob("*.md")}):
        content = preprocess_content(path.read_text(encoding="utf-8"))
        for doc in document_splitter_service.split_document(content, str(path)):
            md = doc.metadata
            chunks.append({"document_id": md["_document_id"], "document_version": md["_document_version"],
                           "parent_id": md["_parent_id"], "chunk_id": md["_chunk_id"],
                           "source": md["_source_key"], "title": md["_document_title"],
                           "section": md["_header_path"], "text": doc.page_content,
                           "parent_text": md["_parent_content"], "document_text": content})
    return {"schema_version": 1, "corpus_hash": fingerprint(chunks), "chunks": chunks}


def dense_run(corpus: dict, queries: list[dict], top_n: int) -> dict:
    from app.services.vector_embedding_service import vector_embedding_service as embedding

    vectors = embedding.embed_documents([row["text"] for row in corpus["chunks"]])
    run = {}
    for query in queries:
        vector = embedding.embed_query(query["text"])
        ranked = sorted(zip(corpus["chunks"], vectors, strict=True),
                        key=lambda row: sum(a * b for a, b in zip(vector, row[1], strict=True)), reverse=True)
        run[query["id"]] = [{"chunk_id": row["chunk_id"],
                             "score": sum(a * b for a, b in zip(vector, values, strict=True))}
                            for row, values in ranked[:top_n]]
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("snapshot")
    command.add_argument("directories", nargs="+", type=Path)
    command.add_argument("--output", required=True, type=Path)
    command = commands.add_parser("pool")
    command.add_argument("--corpus", required=True, type=Path)
    command.add_argument("--queries", required=True, type=Path)
    command.add_argument("--output", required=True, type=Path)
    command.add_argument("--annotations", required=True, type=Path)
    command.add_argument("--top-n", default=20, type=int)
    command.add_argument("--granularity", default="parent", choices=["parent", "chunk", "document"])
    command.add_argument("--dense", action="store_true", help="Use local project embeddings")
    command.add_argument("--route", action="append", default=[], metavar="NAME=RUN_JSON",
                         help="Import independent Dense/expanded-query rankings with corpus_hash and rankings")
    command = commands.add_parser("freeze")
    command.add_argument("--pool", required=True, type=Path)
    command.add_argument("--annotations", required=True, type=Path)
    command.add_argument("--version", required=True)
    command.add_argument("--threshold", default=1, type=int, choices=[1, 2])
    command.add_argument("--output", required=True, type=Path)
    command = commands.add_parser("evaluate")
    command.add_argument("--dataset", required=True, type=Path)
    command.add_argument("--run", required=True, type=Path)
    command.add_argument("--k", default=5, type=int)
    command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "snapshot":
        write(args.output, snapshot(args.directories))
    elif args.command == "pool":
        corpus, queries = read(args.corpus), read(args.queries)
        routes = {}
        for value in args.route:
            name, path = value.split("=", 1)
            run = read(path)
            if run["corpus_hash"] != corpus["corpus_hash"] or name in routes:
                raise ValueError("Route corpus differs or route name is duplicated")
            if set(run["rankings"]) != {query["id"] for query in queries}:
                raise ValueError("Imported route must include all query IDs")
            routes[name] = run["rankings"]
        if args.dense:
            routes["dense"] = dense_run(corpus, queries, args.top_n)
        pool = build_pool(corpus, queries, top_n=args.top_n, dense_runs=routes, granularity=args.granularity)
        if args.annotations.exists():
            raise FileExistsError(args.annotations)
        write(args.output, pool)
        export_annotations(pool, args.annotations)
        print(f"Pool saved; dense_included={pool['dense_included']}. Grades remain unjudged.")
    elif args.command == "freeze":
        freeze_dataset(read(args.pool), args.annotations, version=args.version,
                       output=args.output, threshold=args.threshold)
    else:
        report = evaluate_dataset(read(args.dataset), read(args.run), k=args.k)
        write(args.output, report)
        print(json.dumps(report["summary"], ensure_ascii=False))
        if not report["comparable"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
