"""把检索结果格式化为事实、证据和推断边界明确的上下文。"""

from langchain_core.documents import Document


def format_docs(docs: list[Document]) -> str:
    parts: list[str] = []
    for index, doc in enumerate(docs, 1):
        metadata = doc.metadata
        headers = [str(metadata[key]) for key in ("h1", "h2", "h3") if metadata.get(key)]
        lines = [f"【参考资料 {index}】"]
        if headers:
            lines.append(f"标题: {' > '.join(headers)}")
        lines.extend(
            [
                f"来源: {metadata.get('_file_name', '未知来源')}",
                f"来源类型: {metadata.get('_source_type', '未知')} / 可信等级: {metadata.get('_trust_level', '未知')}",
                f"内容:\n{doc.page_content}",
            ]
        )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def format_diagnostic_context(query: str, outcome) -> str:
    diagnostics = outcome.confidence
    status = "证据基本充分" if diagnostics.level != "low" else "证据不足，禁止确定性归因"
    evidence = format_docs(outcome.documents) if outcome.documents else "未检索到可用知识库证据。"
    return f"""## 日志与告警事实

以下内容来自本次输入，只能视为已观测事实：

{query}

## 知识库证据

- 检索状态：{status}
- 置信度：{diagnostics.score:.3f}（{diagnostics.level}）
- 检索次数：{outcome.attempts}
- 查询是否改写：{outcome.rewritten}
- 是否使用 Cross-Encoder：{outcome.reranker_used}

{evidence}

## 推断约束

- 只能把“日志与告警事实”写成确定事实。
- 知识库内容用于说明可能原因和排查方法，不代表已经证明本次根因。
- 根因必须同时引用本次日志事实和对应知识库证据；否则标为“推测”。
- 检索状态为证据不足时，必须输出“原因未确定”以及需要补充的日志、dump或系统指标。"""
