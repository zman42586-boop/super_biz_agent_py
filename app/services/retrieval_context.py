"""把检索结果格式化为事实、证据和推断边界明确的上下文。"""

from langchain_core.documents import Document

_PLANNER_EVIDENCE_TOTAL_CHARS = 2_400
_PLANNER_EVIDENCE_PER_DOC_CHARS = 600


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


def format_planner_context(query: str, outcome) -> str:
    """为 Planner 提供受预算约束的证据摘要。

    Planner 只需要知道可参考的故障现象和排查方向，不需要在制定计划时读取
    整个 Parent。实际执行检索工具时仍返回完整诊断上下文，避免削弱最终证据。
    """
    diagnostics = outcome.confidence
    remaining = _PLANNER_EVIDENCE_TOTAL_CHARS
    parts = [
        "## 检索经验摘要（仅用于制定计划，不是本次事故的已证实事实）",
        f"- 检索置信度：{diagnostics.score:.3f}（{diagnostics.level}）",
        f"- 查询次数：{outcome.attempts}；是否改写：{outcome.rewritten}",
    ]

    for index, doc in enumerate(outcome.documents, 1):
        if remaining <= 0:
            break
        metadata = doc.metadata
        evidence = str(
            metadata.get("_matched_child")
            or metadata.get("_child_content")
            or doc.page_content
        ).strip()
        limit = min(_PLANNER_EVIDENCE_PER_DOC_CHARS, remaining)
        if len(evidence) > limit:
            evidence = evidence[:limit].rstrip() + "…"
        remaining -= len(evidence)

        headers = [
            str(metadata[key])
            for key in ("h1", "h2", "h3")
            if metadata.get(key)
        ]
        title = " > ".join(headers) or str(metadata.get("_file_name", "未知来源"))
        parts.append(
            "\n".join(
                [
                    f"【经验 {index}】{title}",
                    f"来源: {metadata.get('_file_name', '未知来源')} / "
                    f"可信等级: {metadata.get('_trust_level', '未知')}",
                    f"命中片段: {evidence}",
                ]
            )
        )

    if not outcome.documents:
        parts.append("未检索到可用经验；计划应优先补充日志、dump 或系统指标。")

    context = "\n\n".join(parts)
    max_chars = _PLANNER_EVIDENCE_TOTAL_CHARS + 600
    if len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\n\n…（其余经验摘要已省略）"
    return context
