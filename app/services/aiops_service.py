"""
通用 Plan-Execute-Replan 服务
基于 LangGraph 官方教程实现
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from datetime import UTC, datetime, timedelta

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from loguru import logger

from app.agent.aiops import PlanExecuteState, executor, microcompact, planner, replanner
from app.agent.aiops.evidence import register, seed_evidence, utc_time
from app.agent.aiops.verifier import evidence_verifier, final_report
from app.harness.config import harness_settings
from app.harness.repository import LeaseLostError
from app.harness.runtime import get_run_context
from app.memory import memory_writer
from app.utils.token_meter import count_steps_tokens, log_compression

if TYPE_CHECKING:
    from app.models.alert import AlertRecord

# 节点名称常量
NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_MICROCOMPACT = "microcompact"
NODE_REPLANNER = "replanner"

# Level 4 Autocompact：past_steps 总 token 超过此值时打印统计（演示模式：300 tokens，第一步执行完就触发）
_AUTOCOMPACT_TOKEN_THRESHOLD = 300


class AIOpsService:
    """通用 Plan-Execute-Replan 服务"""

    def __init__(self):
        """初始化服务"""
        self.checkpointer = MemorySaver()
        self.graph = self._build_graph()
        logger.info("Plan-Execute-Replan Service 初始化完成")

    def _build_graph(self):
        workflow = StateGraph(PlanExecuteState)
        nodes = {NODE_PLANNER: (planner, NODE_EXECUTOR),
                 NODE_EXECUTOR: (executor, NODE_MICROCOMPACT),
                 NODE_MICROCOMPACT: (microcompact, NODE_REPLANNER),
                 NODE_REPLANNER: (replanner, "verifier"),
                 "verifier": (evidence_verifier, "final_report"),
                 "final_report": (final_report, "complete")}

        def wrap(function, next_phase):
            async def node(state: PlanExecuteState, config: RunnableConfig):
                deadline = utc_time(state.get("deadline_at"))
                if next_phase != "complete" and deadline and datetime.now(UTC) >= deadline:
                    return await final_report({**state, "candidate_verified": False,
                                               "safe_stop_reason": "累计执行时限耗尽",
                                               "verification": {"status": "NON_SUPPORTED", "reason": "累计执行时限耗尽"}})
                # Microcompact is the only existing node needing RunnableConfig.
                output = await function(state, config) if function is microcompact else await function(state)
                output = dict(output or {})
                output.setdefault("phase", next_phase)
                return output
            return node

        for name, (function, phase) in nodes.items():
            workflow.add_node(name, wrap(function, phase))

        def choose_entry(state):
            phase = state.get("phase")
            if phase == "complete" and state.get("response"):
                return END
            if phase in nodes:
                return phase
            # Old snapshots have no durable graph phase. Legacy prose cannot
            # bypass verification; regenerate a structured candidate instead.
            if state.get("response"):
                return NODE_REPLANNER
            if state.get("plan"):
                return NODE_EXECUTOR
            if state.get("past_steps"):
                return NODE_REPLANNER
            return NODE_PLANNER

        def route(state):
            phase = state.get("phase")
            return END if phase == "complete" else phase

        targets = {name: name for name in nodes}
        targets[END] = END
        workflow.add_conditional_edges(START, choose_entry, targets)
        for name in nodes:
            workflow.add_conditional_edges(name, route, targets)
        return workflow.compile(checkpointer=self.checkpointer)

    async def execute(
        self,
        user_input: str,
        session_id: str = "default",
        initial_state: PlanExecuteState | None = None,
        execution_id: str | None = None,
        evidence_payload: dict | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        执行 Plan-Execute-Replan 流程

        Args:
            user_input: 用户的任务描述
            session_id: 会话ID

        Yields:
            Dict[str, Any]: 流式事件
        """
        logger.info(f"[会话 {session_id}] 开始执行任务: {user_input}")

        try:
            # 初始化状态；Harness 恢复时传入最后一次持久化快照。
            graph_input: PlanExecuteState = dict(initial_state) if initial_state else {
                "input": user_input,
                "plan": [],
                "past_steps": [],
                "response": "",
                "steps_summary": "",
                "loop_guard": {},
            }
            graph_input["input"] = graph_input.get("input") or user_input
            context = get_run_context()
            payload = evidence_payload or {}
            if context:
                run = await asyncio.to_thread(context.repository.get_run, context.run_id)
                payload = run["input"]
            alert = payload.get("alert") or {}
            diagnosis_context = graph_input.get("diagnosis_context") or {
                "run_id": context.run_id if context else execution_id or session_id,
                "host_id": payload.get("host") or alert.get("host", ""),
                "event_time": alert.get("ts"),
                "evaluation_mode": bool(payload.get("evaluation_mode", False)),
            }
            graph_input["diagnosis_context"] = diagnosis_context
            graph_input["evidence_registry"] = register(graph_input.get("evidence_registry", {}),
                                                        list(seed_evidence(payload, diagnosis_context).values()))
            graph_input.setdefault("deadline_at", (datetime.now(UTC) + timedelta(
                seconds=harness_settings.run_timeout_seconds)).isoformat())
            if context:
                await asyncio.to_thread(context.repository.merge_checkpoint, context.run_id,
                                        "evidence_initialized", {
                                            "diagnosis_context": diagnosis_context,
                                            "evidence_registry": graph_input["evidence_registry"],
                                            "deadline_at": graph_input["deadline_at"],
                                        })


            # 流式执行工作流
            config_dict = {
                "configurable": {"thread_id": execution_id or session_id},
                "recursion_limit": max(harness_settings.graph_recursion_limit,
                                       3 * harness_settings.max_steps +
                                       2 * (1 + harness_settings.verification_max_corrections) + 4),
            }

            async for event in self.graph.astream(
                input=graph_input, config=config_dict, stream_mode="updates"
            ):
                # 解析事件
                for node_name, node_output in event.items():
                    logger.info(f"节点 '{node_name}' 输出事件")

                    # Run/Step 持久化独立于 LangGraph 的进程内 MemorySaver。
                    run_context = get_run_context()
                    if run_context is not None and node_output:
                        await asyncio.to_thread(
                            run_context.repository.merge_checkpoint,
                            run_context.run_id,
                            node_name,
                            node_output,
                        )

                    # 根据节点类型生成不同的事件
                    if node_name == NODE_PLANNER:
                        yield self._format_planner_event(node_output)

                    elif node_name == NODE_EXECUTOR:
                        yield self._format_executor_event(node_output)

                    elif node_name == NODE_MICROCOMPACT:
                        pass  # microcompact 内部已打印 TokenMeter，此处静默

                    elif node_name == "verifier":
                        yield {"type": "verification", "stage": "evidence_verification",
                               "verification": node_output.get("verification", {}),
                               "attempt": node_output.get("verification_attempts", 0)}
                    elif node_name == "final_report":
                        yield {"type": "report", "stage": "final_report",
                               "message": "验证流程结束，诊断报告已生成", "report": node_output.get("response", "")}
                    elif node_name == NODE_REPLANNER:
                        # Level 4 Autocompact：检查累计 past_steps token 数
                        if node_output:
                            past_steps = node_output.get("past_steps", [])
                            if past_steps:
                                total_tokens = count_steps_tokens(past_steps)
                                if total_tokens > _AUTOCOMPACT_TOKEN_THRESHOLD:
                                    log_compression(
                                        "Autocompact",
                                        total_tokens,
                                        total_tokens,  # 已由 microcompact/collapse 处理
                                    )
                                    logger.warning(
                                        f"[Autocompact] past_steps 累计 {total_tokens} tokens"
                                        f"（阈值 {_AUTOCOMPACT_TOKEN_THRESHOLD}），"
                                        "已由 Microcompact/Collapse 压缩"
                                    )
                        yield self._format_replanner_event(node_output)

            # 获取最终状态
            final_state = self.graph.get_state(config_dict)
            final_response = ""

            # 安全地获取响应（处理 values 可能为 None 的情况）
            if final_state and final_state.values:
                final_response = final_state.values.get("response", "")

            logger.info(f"[会话 {session_id}] 任务执行完成")

            # 长期记忆：将诊断结果写入 memory/incidents/
            # 必须在 yield complete 之前执行，否则上层生成器 break 会导致保存不执行
            run_context = get_run_context()
            if run_context and run_context.offline_host:
                from app.harness.host_report import format_host_report

                durable_run = await asyncio.to_thread(
                    run_context.repository.get_run, run_context.run_id
                )
                final_response = await asyncio.to_thread(
                    format_host_report, durable_run, final_response, run_context.repository
                )
            if run_context is not None:
                owned = await asyncio.to_thread(
                    run_context.repository.check_ownership,
                    run_context.run_id,
                    run_context.worker_id,
                    run_context.version,
                )
                if not owned:
                    raise LeaseLostError("Lost lease before incident write")
            if final_response and not session_id.startswith("alert_eval_"):
                try:
                    await memory_writer.save_incident(
                        session_id=(
                            f"{session_id}_version_{run_context.version}"
                            if run_context
                            else session_id
                        ),
                        task_description=user_input[:120],
                        report=final_response,
                    )
                except LeaseLostError:
                    raise
                except Exception as mem_err:
                    logger.warning(f"[MemoryWriter] 保存 incident 失败（不影响主流程）: {mem_err}")

            # 发送完成事件
            yield {
                "type": "complete",
                "stage": "complete",
                "message": "任务执行完成",
                "host_report_finalized": bool(run_context and run_context.offline_host),
                "response": final_response,
            }

        except LeaseLostError:
            raise
        except Exception as e:
            logger.error(f"[会话 {session_id}] 任务执行失败: {e}", exc_info=True)
            yield {
                "type": "error",
                "stage": "error",
                "error_code": (
                    "GRAPH_RECURSION_LIMIT"
                    if isinstance(e, GraphRecursionError)
                    else "AGENT_EXECUTION_FAILED"
                ),
                "message": f"任务执行出错: {str(e)}",
            }

    async def diagnose(self, session_id: str = "default") -> AsyncGenerator[dict[str, Any], None]:
        """
        AIOps 诊断接口（兼容旧接口）

        Args:
            session_id: 会话ID

        Yields:
            Dict[str, Any]: 诊断过程的流式事件
        """
        # 使用固定的 AIOps 任务描述
        from textwrap import dedent

        aiops_task = dedent("""诊断当前系统是否存在告警，如果存在告警请详细分析告警原因并生成诊断报告，诊断报告输出格式要求：
                ```
                # 告警分析报告

                ---

                ## 📋 活跃告警清单

                | 告警名称 | 级别 | 目标服务 | 首次触发时间 | 最新触发时间 | 状态 |
                |---------|------|----------|-------------|-------------|------|
                | [告警1名称] | [级别] | [服务名] | [时间] | [时间] | 活跃 |
                | [告警2名称] | [级别] | [服务名] | [时间] | [时间] | 活跃 |

                ---

                ## 🔍 告警根因分析1 - [告警名称]

                ### 告警详情
                - **告警级别**: [级别]
                - **受影响服务**: [服务名]
                - **持续时间**: [X分钟]

                ### 症状描述
                [根据监控指标描述症状]

                ### 日志证据
                [引用查询到的关键日志]

                ### 根因结论
                [基于证据得出的根本原因]

                ---

                ## 🛠️ 处理方案执行1 - [告警名称]

                ### 已执行的排查步骤
                1. [步骤1]
                2. [步骤2]

                ### 处理建议
                [给出具体的处理建议]

                ### 预期效果
                [说明预期的效果]

                ---

                ## 🔍 告警根因分析2 - [告警名称]
                [如果有第2个告警，重复上述格式]

                ---

                ## 📊 结论

                ### 整体评估
                [总结所有告警的整体情况]

                ### 关键发现
                - [发现1]
                - [发现2]

                ### 后续建议
                1. [建议1]
                2. [建议2]

                ### 风险评估
                [评估当前风险等级和影响范围]
                ```

                **重要提醒**：
                - 最终输出必须是纯 Markdown 文本，不要包含 JSON 结构
                - 所有内容必须基于工具查询的真实数据，严禁编造
                - 如果某个步骤失败，在结论中如实说明，不要跳过""")

        async for event in self.execute(aiops_task, session_id):
            # 转换事件格式以兼容旧的 API
            if event.get("type") == "complete":
                # 将 response 包装为 diagnosis 格式
                yield {
                    "type": "complete",
                    "stage": "diagnosis_complete",
                    "message": "诊断流程完成",
                    "diagnosis": {"status": "completed", "report": event.get("response", "")},
                }
            else:
                yield event

    async def execute_alert_diagnosis(
        self,
        alert: "AlertRecord",
        *,
        initial_state: PlanExecuteState | None = None,
        execution_id: str | None = None,
        evaluation_mode: bool = False,
    ) -> str:
        """
        根据真实告警 payload 构造明确 prompt，运行 Plan-Execute-Replan 诊断，
        返回最终报告文本。支持温度告警和进程崩溃告警两类场景。
        """
        from app.services.alert_service import build_alert_diagnosis_task

        task = build_alert_diagnosis_task(alert)

        session_id = f"alert_{alert.alert_id}"
        report = ""
        async for event in self.execute(
            task,
            session_id,
            initial_state=initial_state,
            execution_id=execution_id,
            evidence_payload={"alert": alert.model_dump(mode="json"), "evaluation_mode": evaluation_mode},
        ):
            if event.get("type") == "complete":
                report = event.get("response", "")
                break
            if event.get("type") == "error":
                from app.harness.worker import RunExecutionError

                raise RunExecutionError(
                    event.get("error_code", "AGENT_EXECUTION_FAILED"),
                    event.get("message", "Graph failed"),
                )
            if event.get("type") == "report":
                report = event.get("report", "")

        return report or "诊断未返回报告内容"

    def _format_planner_event(self, state: dict | None) -> dict:
        """格式化 Planner 节点事件"""
        if not state:
            return {"type": "status", "stage": "planner", "message": "规划节点执行中"}

        plan = state.get("plan", [])

        return {
            "type": "plan",
            "stage": "plan_created",
            "message": f"执行计划已制定，共 {len(plan)} 个步骤",
            "plan": plan,
        }

    def _format_executor_event(self, state: dict | None) -> dict:
        """格式化 Executor 节点事件"""
        if not state:
            return {"type": "status", "stage": "executor", "message": "执行节点运行中"}

        plan = state.get("plan", [])
        past_steps = state.get("past_steps", [])

        if past_steps:
            last_step, _ = past_steps[-1]
            return {
                "type": "step_complete",
                "stage": "step_executed",
                "message": f"步骤执行完成 ({len(past_steps)}/{len(past_steps) + len(plan)})",
                "current_step": last_step,
                "remaining_steps": len(plan),
            }
        else:
            return {"type": "status", "stage": "executor", "message": "开始执行步骤"}

    def _format_replanner_event(self, state: dict | None) -> dict:
        """格式化 Replanner 节点事件"""
        if not state:
            return {"type": "status", "stage": "replanner", "message": "评估节点运行中"}

        return {"type": "status", "stage": "replanner",
                "message": "候选诊断待验证" if state.get("candidate_diagnosis") else "正在调整调查计划",
                "remaining_steps": len(state.get("plan", []))}


# 全局单例
aiops_service = AIOpsService()
