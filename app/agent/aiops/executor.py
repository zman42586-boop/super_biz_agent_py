"""
Executor 节点：执行单个步骤
基于 LangGraph 官方教程实现
"""

import asyncio
import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from app.agent.mcp_client import get_mcp_tools_with_circuit_breaker
from app.claude_skills import list_skill_ids
from app.core.llm_factory import llm_factory
from app.harness.loop_guard import loop_guard
from app.harness.runtime import get_run_context
from app.harness.tool_gateway import ToolExecutionError, tool_gateway
from app.tools import get_current_time, retrieve_knowledge, search_log

from .state import PlanExecuteState


async def executor(state: PlanExecuteState) -> dict[str, Any]:
    """
    执行节点：执行计划中的下一个步骤

    所有工具调用统一经过 Harness Tool Gateway
    """
    logger.info("=== Executor：执行步骤 ===")

    plan = state.get("plan", [])

    # 如果计划为空，不执行
    if not plan:
        logger.info("计划为空，跳过执行")
        return {}

    # 取出第一个步骤
    task = plan[0]
    logger.info(f"当前任务: {task}")

    current_past_steps = list(state.get("past_steps", []))
    run_context = get_run_context()
    guard_decision = loop_guard.evaluate_step(task, current_past_steps)
    if not guard_decision.allowed:
        payload = guard_decision.event_payload("step")
        logger.warning(f"[LoopGuard] {payload}")
        if run_context is not None:
            await asyncio.to_thread(
                run_context.repository.append_event,
                run_context.run_id,
                "loop_guard_triggered",
                payload,
            )
        previous_guard = dict(state.get("loop_guard") or {})
        guard_state = {
            **payload,
            "trigger_count": int(previous_guard.get("trigger_count", 0)) + 1,
        }
        return {
            "plan": [],
            "past_steps": current_past_steps
            + [(task, f"LoopGuard 已停止该步骤：{guard_decision.message}")],
            "loop_guard": guard_state,
        }

    # Skill 识别：若步骤包含 .claude/skills 中声明的 id，记录日志
    for skill_id in list_skill_ids():
        if skill_id in task:
            logger.info(f"检测到 Skill 步骤: {skill_id}，将按 Skill 文档推荐流程执行")
            break

    step_row = None
    step_index = len(current_past_steps)
    if run_context is not None:
        step_row = await asyncio.to_thread(
            run_context.repository.start_step,
            run_context.run_id,
            step_index,
            task,
            {"task": task},
        )
        if step_row["status"] == "succeeded":
            stored = step_row.get("output") or {}
            stored_result = str(stored.get("content", ""))
            return {
                "plan": plan[1:],
                "past_steps": current_past_steps + [(task, stored_result)],
            }

    try:
        # 获取本地工具
        local_tools = [
            get_current_time,
            retrieve_knowledge,
            search_log,
        ]

        # 获取 MCP 工具（失败时降级为仅本地工具）
        try:
            mcp_tools = await get_mcp_tools_with_circuit_breaker()
        except Exception as e:
            logger.warning(f"获取 MCP 工具失败: {e}")
            mcp_tools = []
        logger.info(f"可用工具数量: 本地 {len(local_tools)} + MCP {len(mcp_tools)}")

        # 合并所有工具
        all_tools = local_tools + mcp_tools

        # 创建 LLM（绑定工具）
        llm = llm_factory.create_chat_model(temperature=0)
        llm_with_tools = llm.bind_tools(all_tools)

        # 构建消息（只包含当前步骤，避免原始任务干扰）
        messages = [
            SystemMessage(content="""你是一个能力强大的助手，负责执行具体的任务步骤。

你可以使用各种工具来完成任务。对于每个步骤：
1. 理解步骤的目标
2. 选择合适的工具，如果已经指定了工具，则使用指定的工具
3. 调用工具获取信息
4. 返回执行结果

注意：
- 如果工具调用失败，请说明失败原因
- 不要编造数据，只返回实际获取的信息
- 严格区分日志事实、知识库证据和分析推断；证据不足时明确写“原因未确定”
- 执行结果要清晰、准确
- 专注于当前步骤，不要考虑其他任务"""),
            HumanMessage(content=f"请执行以下任务: {task}")
        ]

        # 第一步：LLM 决定是否调用工具
        llm_response = await llm_with_tools.ainvoke(messages)
        logger.info(f"LLM 响应类型: {type(llm_response)}")

        # 第二步：如果有工具调用，执行工具
        if hasattr(llm_response, "tool_calls") and llm_response.tool_calls:
            logger.info(f"检测到 {len(llm_response.tool_calls)} 个工具调用")

            messages.append(llm_response)
            tools_by_name = {
                str(getattr(tool, "name", getattr(tool, "__name__", ""))): tool
                for tool in all_tools
            }
            tool_messages = []
            for tool_call in llm_response.tool_calls:
                tool_name = str(tool_call.get("name", ""))
                tool_call_id = str(tool_call.get("id", tool_name))
                arguments = tool_call.get("args") or {}
                tool = tools_by_name.get(tool_name)
                if tool is None:
                    content = (
                        f'{{"ok": false, "error_code": "TOOL_NOT_FOUND", '
                        f'"message": "未知工具: {tool_name}"}}'
                    )
                else:
                    try:
                        execution = await tool_gateway.execute(
                            tool=tool,
                            arguments=arguments,
                            step_id=int(step_row["id"]) if step_row else None,
                            step_index=step_index,
                        )
                        content = execution.content
                    except ToolExecutionError as tool_error:
                        content = (
                            f'{{"ok": false, "error_code": "{tool_error.code}", '
                            f'"message": {json.dumps(str(tool_error), ensure_ascii=False)}}}'
                        )
                tool_messages.append(
                    ToolMessage(
                        content=content,
                        tool_call_id=tool_call_id,
                        name=tool_name or None,
                    )
                )

            # 第三步：将工具结果返回给 LLM 生成最终答案
            messages.extend(tool_messages)
            final_response = await llm_with_tools.ainvoke(messages)
            result = final_response.content if hasattr(final_response, 'content') else str(final_response)
        else:
            # 没有工具调用，直接使用 LLM 的输出
            logger.info("LLM 未调用工具，直接返回结果")
            result = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)

        result = result if isinstance(result, str) else str(result)
        logger.info(f"步骤执行完成，结果长度: {len(result)}")

        if run_context is not None and step_row is not None:
            await asyncio.to_thread(
                run_context.repository.complete_step,
                int(step_row["id"]),
                output=result,
            )

        # past_steps 使用全量替换 reducer，必须返回完整列表
        return {
            "plan": plan[1:],
            "past_steps": current_past_steps + [(task, result)],
        }

    except Exception as e:
        logger.error(f"执行步骤失败: {e}", exc_info=True)
        if run_context is not None and step_row is not None:
            await asyncio.to_thread(
                run_context.repository.fail_step,
                int(step_row["id"]),
                "STEP_EXECUTION_FAILED",
                str(e),
            )
        return {
            "plan": plan[1:],
            "past_steps": current_past_steps + [(task, f"执行失败: {str(e)}")],
        }
