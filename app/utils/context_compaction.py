"""纯文本上下文压缩工具，不依赖模型、数据库或 Agent 运行时。"""

from __future__ import annotations

from app.utils.token_meter import count_steps_tokens, count_tokens, log_compression

COLLAPSE_STEPS_THRESHOLD = 1


def collapse_past_steps(past_steps: list) -> tuple[str, str]:
    """将旧步骤压缩为摘要，保留最近两步的有限预览。"""
    if not past_steps:
        return "", ""

    if len(past_steps) <= COLLAPSE_STEPS_THRESHOLD:
        summary = "\n".join(
            f"步骤: {step}\n结果: {str(result)[:300]}..."
            for step, result in past_steps
        )
        return summary, summary

    before_tokens = count_steps_tokens(past_steps)
    older = past_steps[:-2]
    recent = past_steps[-2:]

    older_lines = [
        f"[折叠] 步骤{i + 1}: {step} → {str(result)[:120]}..."
        for i, (step, result) in enumerate(older)
    ]
    recent_lines = [
        f"步骤: {step}\n结果: {str(result)[:500]}{'...' if len(str(result)) > 500 else ''}"
        for step, result in recent
    ]

    state_summary = "[历史步骤摘要]\n" + "\n".join(older_lines)
    prompt_summary = (
        "[历史步骤（已折叠）]\n"
        + "\n".join(older_lines)
        + "\n\n[最近步骤详情]\n"
        + "\n\n".join(recent_lines)
    )
    log_compression("Collapse", before_tokens, count_tokens(prompt_summary))
    return state_summary, prompt_summary


def format_execution_history(past_steps: list) -> str:
    """返回最终报告使用的有界步骤历史。"""
    if not past_steps:
        return "无已执行步骤。"
    _, prompt_summary = collapse_past_steps(past_steps)
    return prompt_summary
