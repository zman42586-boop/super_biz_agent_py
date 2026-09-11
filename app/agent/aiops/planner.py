"""
Planner 节点：制定执行计划
基于 LangGraph 官方教程实现
"""

from textwrap import dedent
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.mcp_client import get_mcp_client_with_retry
from app.claude_skills import load_skills_description
from app.core.llm_factory import llm_factory
from app.harness.config import harness_settings
from app.memory import load_memory_context
from app.tools import get_current_time, retrieve_knowledge, search_log
from app.utils.token_meter import log_token_budget

from .state import PlanExecuteState
from .utils import format_tools_description


class Plan(BaseModel):
    """计划的输出格式"""
    steps: list[str] = Field(
        description="完成任务所需的不同步骤。这些步骤应该按顺序执行，每一步都建立在前一步的基础上。"
    )


# Planner 提示词
planner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                作为一个专家级别的规划者，你需要将复杂的任务分解为可执行的步骤。

                可用工具列表（用于制定计划时参考）：

                {tools_description}

                可用 Skill（标准化诊断流程，优先引用）：

                {skills_description}

                注意：你的职责是制定计划，实际的工具调用由 Executor 负责执行。

                证据规则：
                - 严格区分“已观测事实”“知识库证据”“分析推断”
                - 知识库中的可能原因不等于本次事故的已证实根因
                - 检索失败或证据不足时，计划中必须保留补充日志/dump/指标的步骤，并标记“原因未确定”
                - 不得为了完成计划而编造根因

                {memory_context}

                {experience_context}

                对于给定的任务，请创建一个简单的、逐步的计划来完成它。计划应该：
                - 将任务分解为逻辑上独立的步骤
                - 每个步骤应该明确使用哪些工具(如果需要工具的话)来获取信息, 最好能同时提供工具执行所需要的参数
                - 步骤之间应该有清晰的依赖关系
                - 步骤描述要具体、可操作
                - **如果有相关经验文档，请参考其中的方法和步骤制定计划**

                示例输入："分析当前系统的性能问题"
                示例输出（假设有对应工具）：
                步骤1: 使用 get_metrics 工具收集系统的 CPU 和内存使用情况
                步骤2: 使用 query_logs 工具检查最近的错误日志
                步骤3: 使用 query_database 工具分析慢查询日志
                步骤4: 综合以上信息生成性能分析报告
            """).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)


async def planner(state: PlanExecuteState) -> dict[str, Any]:
    """
    规划节点：根据用户输入生成执行计划

    流程：
    1. 先查询内部文档，获取相关经验和最佳实践
    2. 基于经验文档和可用工具制定执行计划
    """
    logger.info("=== Planner：制定执行计划 ===")

    input_text = state.get("input", "")
    logger.info(f"用户输入: {input_text}")

    try:
        # 步骤0: 读取 Hot 记忆（仅 MEMORY.md：摘要 + 报告链接）
        memory_ctx = load_memory_context()
        if memory_ctx:
            memory_context = dedent(f"""
                ## 长期记忆参考

                以下是系统保存的历史诊断记录和主题知识，请在制定计划时参考：

                {memory_ctx}

                ---
            """).strip()
            logger.info(f"Planner 已加载长期记忆，长度: {len(memory_ctx)}")
        else:
            memory_context = ""

        # 步骤1: 查询内部文档获取相关经验
        logger.info("查询内部文档，寻找相关经验...")
        experience_docs = ""
        try:
            # retrieve_knowledge 使用 response_format="content_and_artifact"
            # ainvoke() 只返回 content（字符串），不是元组
            context_str = await retrieve_knowledge.ainvoke(
                {"query": input_text, "mode": "plan"}
            )
            if context_str and context_str.strip():
                experience_docs = context_str
                logger.info(f"找到相关经验文档，长度: {len(experience_docs)}")
            else:
                logger.info("未找到相关经验文档")
        except Exception as e:
            logger.warning(f"查询内部文档失败: {e}")

        # 步骤2: 获取可用工具列表
        # 获取本地工具
        local_tools = [
            get_current_time,
            retrieve_knowledge,
            search_log,
        ]

        # 获取 MCP 工具（失败时降级为仅本地工具）
        try:
            mcp_client = await get_mcp_client_with_retry()
            mcp_tools = await mcp_client.get_tools()
        except Exception as e:
            logger.warning(f"获取 MCP 工具失败: {e}")
            mcp_tools = []

        # 合并所有工具
        all_tools = local_tools + mcp_tools
        logger.info(f"可用工具数量: 本地 {len(local_tools)} + MCP {len(mcp_tools)}")

        # 格式化工具描述
        tools_description = format_tools_description(all_tools)

        # 步骤2.5: 从 .claude/skills/*.md 加载 Skill 描述（Claude Code 风格）
        skills_description = load_skills_description()

        # 步骤3: 格式化经验文档上下文
        if experience_docs:
            experience_context = dedent(f"""
                ## 相关经验文档

                以下是从知识库中检索到的相关经验和最佳实践，请参考这些经验制定执行计划：

                {experience_docs}

                ---
            """).strip()
        else:
            experience_context = ""

        # 步骤4: 创建 LLM 并生成计划
        llm = llm_factory.create_chat_model(
            temperature=0,
            streaming=False,
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=1024,
        )

        planner_chain = planner_prompt | llm.with_structured_output(Plan, method="function_calling")

        log_token_budget(
            "PlannerInput",
            {
                "task": input_text,
                "tools": tools_description,
                "skills": skills_description,
                "memory": memory_context,
                "experience": experience_context,
            },
        )

        # 调用 LLM 生成计划
        plan_result = await planner_chain.ainvoke({
            "messages": [("user", input_text)],
            "tools_description": tools_description,
            "skills_description": skills_description,
            "memory_context": memory_context,
            "experience_context": experience_context,
        })

        # 提取步骤列表
        if isinstance(plan_result, Plan):
            plan_steps = plan_result.steps
        else:
            # 如果返回的是字典，提取 steps 字段
            plan_steps = plan_result.get("steps", [])  # type: ignore

        if len(plan_steps) > harness_settings.max_steps:
            logger.warning(
                f"Planner 生成 {len(plan_steps)} 个步骤，"
                f"按 LoopGuard 限制截断为 {harness_settings.max_steps} 个"
            )
            plan_steps = plan_steps[: harness_settings.max_steps]

        logger.info(f"计划已生成，共 {len(plan_steps)} 个步骤")
        for i, step in enumerate(plan_steps, 1):
            logger.info(f"  步骤{i}: {step}")

        return {"plan": plan_steps}

    except Exception as e:
        logger.error(f"生成计划失败: {e}", exc_info=True)
        # 返回一个默认计划
        return {
            "plan": [
                "收集相关信息",
                "分析数据",
                "生成报告"
            ]
        }
