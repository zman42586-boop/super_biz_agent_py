from app.services.document_splitter_service import DocumentSplitterService


def test_markdown_parent_child_preserves_section_and_token_limit() -> None:
    service = DocumentSplitterService()
    repeated = "MATLAB 内存持续增长，需要检查数组预分配和对象释放。" * 120
    content = f"# MATLAB 运维\n\n## 内存问题\n\n{repeated}\n\n## CPU 问题\n\n使用 profile 定位热点。"

    docs = service.split_markdown(content, "matlab_test.md")

    assert len(docs) > 2
    assert max(doc.metadata["_token_count"] for doc in docs) <= service.chunk_size
    assert all(doc.metadata["_parent_content"] for doc in docs)
    assert all(doc.metadata["_parent_id"] for doc in docs)
    assert any("内存问题" in doc.metadata["_header_path"] for doc in docs)
    assert any("CPU 问题" in doc.metadata["_header_path"] for doc in docs)


def test_case_source_is_not_mislabeled_as_official() -> None:
    service = DocumentSplitterService()
    docs = service.split_markdown(
        "# 案例\n\n## 来源\n\n参考 https://mathworks.com 的处理方法。",
        "case_example.md",
    )

    assert {doc.metadata["_source_type"] for doc in docs} == {"case"}


def test_only_curated_official_filename_gets_high_trust() -> None:
    service = DocumentSplitterService()
    content = "# 指南\n\n参考 https://mathworks.com 的资料。"

    guide = service.split_markdown(content, "matlab_memory_guide.md")
    official = service.split_markdown(content, "matlab_official_memory_runbook.md")

    assert {doc.metadata["_source_type"] for doc in guide} == {"knowledge"}
    assert {doc.metadata["_source_type"] for doc in official} == {"official"}
    assert {doc.metadata["_trust_level"] for doc in official} == {"high"}
