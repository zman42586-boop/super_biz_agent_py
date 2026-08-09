"""LLM-as-Judge — 用 LLM 给 AIOps 诊断报告打分。

使用独立 Judge 模型，通过 Structured Output 返回
评分 (0-10) + 子维度分 + 评价。供 eval_runner.py 调用。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.config import config
from app.core.llm_factory import llm_factory


class EvalScore(BaseModel):
    """评估分数"""

    score: int = Field(description="整体评分 (0-10)")
    accuracy: int = Field(description="根因分析准确性 (0-4)")
    evidence: int = Field(description="证据引用充分性 (0-3)")
    actionable: int = Field(description="建议可操作性 (0-3)")
    comment: str = Field(description="简要点评 (50字以内)")


JUDGE_PROMPT = """你是 AIOps 诊断报告的质量评估裁判。根据以下标准评分:

**场景信息:**
- 真实根因: {scenario_name}
- 预期应包含: {must_mention}
- 预期可包含: {should_mention}
- 不应出现: {must_not_mention}

**诊断报告:**
{report}

**评分标准:**
- 根因分析准确性 (0-4): 是否准确识别了真实根因？偏离真实根因扣分。
- 证据引用充分性 (0-3): 是否引用了具体数据/调用栈/日志？空泛扣分。
- 建议可操作性 (0-3): 处理建议是否具体可执行？"建议排查"这类空洞设议不给分。

整体分数 = accuracy + evidence + actionable (0-10)。

请用 JSON 格式返回评分。"""


def load_scenario(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def judge_report(scenario: dict[str, Any], report: str) -> EvalScore:
    """给定一个 eval 场景和诊断报告，返回 LLM 评分。

    Args:
        scenario: 从 scenario_XX.json 加载的 dict
        report: AIOps 诊断输出的完整 Markdown 文本

    Returns:
        EvalScore: score(0-10) + accuracy + evidence + actionable + comment
    """
    expected = scenario.get("expected", {})
    prompt = JUDGE_PROMPT.format(
        scenario_name=scenario.get("name", ""),
        must_mention=", ".join(expected.get("must_mention", [])),
        should_mention=", ".join(expected.get("should_mention", [])),
        must_not_mention=", ".join(expected.get("must_not_mention", [])),
        report=report[:6000],  # 限制长度，避免超模型上下文
    )

    llm = llm_factory.create_chat_model(
        model=config.eval_judge_model,
        temperature=0,
        streaming=False,
        extra_body={"thinking": {"type": "disabled"}},
        max_tokens=256,
    )
    judge_chain = llm.with_structured_output(EvalScore, method="function_calling")
    result = await judge_chain.ainvoke(prompt)

    if isinstance(result, EvalScore):
        return result
    # fallback: 返回 dict 格式
    return EvalScore(
        score=result.get("score", 0),
        accuracy=result.get("accuracy", 0),
        evidence=result.get("evidence", 0),
        actionable=result.get("actionable", 0),
        comment=result.get("comment", "评分解析失败"),
    )  # type: ignore[arg-type]
