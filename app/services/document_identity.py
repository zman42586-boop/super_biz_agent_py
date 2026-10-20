"""Content-versioned identifiers shared by indexing, evidence and offline evaluation."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_key(source: str) -> str:
    path = Path(source)
    try:
        path = path.resolve().relative_to(ROOT)
    except (ValueError, OSError):
        pass
    return path.as_posix().replace("\\", "/")


def document_metadata(source: str, content: str) -> dict[str, str]:
    key = source_key(source)
    return {"_document_id": digest(key), "_document_version": digest(content), "_source_key": key}


def section_metadata(base: dict, parent_index: int, child_index: int) -> dict[str, str]:
    parent = f"{base['_document_id']}@{base['_document_version']}:p{parent_index}"
    return {"_parent_id": parent, "_chunk_id": f"{parent}:c{child_index}"}
