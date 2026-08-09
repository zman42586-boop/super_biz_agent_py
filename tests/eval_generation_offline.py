"""离线检索知识后调用 LLM 生成诊断，并运行 LLM-as-Judge。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.config import config
from app.core.llm_factory import llm_factory
from app.services.vector_embedding_service import vector_embedding_service
from tests.eval_judge import judge_report
from tests.eval_retrieval import build_eval_query
from tests.eval_retrieval_offline import (
    _load_chunks,
    _load_scenarios,
    _optimized_search,
)


def _generation_prompt(scenario: dict[str, Any], context: str) -> str:
    return f"""你是 MATLAB AIOps 诊断工程师。根据告警证据和检索资料生成简洁诊断报告。

告警：
{json.dumps(scenario['alert'], ensure_ascii=False, indent=2)}

检索资料：
{context}

要求：
1. 区分已知事实、推断和待验证项。
2. 给出最可能根因、关键证据、立即处置和后续修复。
3. 没有足够证据时明确写“原因未确定”，不要臆造日志或工具结果。
4. 使用中文，控制在 800 字以内。
"""


async def run(top_k: int = 5, limit: int | None = None) -> dict[str, Any]:
    chunks = _load_chunks()
    chunk_matrix = np.asarray(
        vector_embedding_service.embed_documents([doc.page_content for doc in chunks]),
        dtype=np.float32,
    )
    scenarios = _load_scenarios()[:limit]
    model = llm_factory.create_chat_model(temperature=0, streaming=False)
    results = []

    for index, scenario in enumerate(scenarios, 1):
        query = build_eval_query(scenario)
        docs = _optimized_search(query, chunks, chunk_matrix, top_k)
        context = "\n\n".join(
            f"[{doc.metadata.get('_file_name', 'unknown')}]\n{doc.page_content}"
            for doc in docs
        )
        response = await model.ainvoke(_generation_prompt(scenario, context))
        report = str(response.content)
        score = await judge_report(scenario, report)
        print(f"[{index}/{len(scenarios)}] {scenario['id']}: {score.score}/10", flush=True)
        results.append(
            {
                "id": scenario["id"],
                "retrieved_docs": [doc.metadata.get("_file_name", "") for doc in docs],
                "report": report,
                "judge": score.model_dump(),
            }
        )

    return {
        "model": model.model_name,
        "judge_model": config.eval_judge_model,
        "scenario_count": len(results),
        "average_score": sum(row["judge"]["score"] for row in results) / len(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args.top_k, args.limit))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
