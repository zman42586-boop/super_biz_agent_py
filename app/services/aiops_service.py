"""
通用 Plan-Execute-Replan 服务
基于 LangGraph 官方教程实现
"""

from typing import AsyncGenerator, Dict, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger

from app.agent.aiops import PlanExecuteState, planner, executor, replanner, microcompact
from app.utils.token_meter import count_steps_tokens, log_compression
from app.memory import memory_writer

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
        """构建 Plan-Execute-Replan 工作流"""
        logger.info("构建工作流图...")

        # 创建状态图
        workflow = StateGraph(PlanExecuteState)

        # 添加节点
        workflow.add_node(NODE_PLANNER, planner)          # 制定计划
        workflow.add_node(NODE_EXECUTOR, executor)        # 执行步骤
        workflow.add_node(NODE_MICROCOMPACT, microcompact)  # Level 2: 压缩大工具结果
        workflow.add_node(NODE_REPLANNER, replanner)      # 重新规划

        # 设置入口点
        workflow.set_entry_point(NODE_PLANNER)

        # 定义边：planner → executor → microcompact → replanner
        workflow.add_edge(NODE_PLANNER, NODE_EXECUTOR)
        workflow.add_edge(NODE_EXECUTOR, NODE_MICROCOMPACT)
        workflow.add_edge(NODE_MICROCOMPACT, NODE_REPLANNER)

        # replanner 的条件边
        def should_continue(state: PlanExecuteState) -> str:
            """判断是否继续执行"""
            # 如果已经生成了最终响应，结束
            if state.get("response"):
                logger.info("已生成最终响应，结束流程")
                return END

            # 如果还有计划步骤，继续执行
            plan = state.get("plan", [])
            if plan:
                logger.info(f"继续执行，剩余 {len(plan)} 个步骤")
                return NODE_EXECUTOR

            # 计划为空但没有响应，返回 replanner 生成响应
            logger.info("计划执行完毕，生成最终响应")
            return END

        workflow.add_conditional_edges(
            NODE_REPLANNER,
            should_continue,
            {
                NODE_EXECUTOR: NODE_EXECUTOR,
                END: END,
            }
        )

        # 编译工作流
        compiled_graph = workflow.compile(checkpointer=self.checkpointer)

        logger.info("工作流图构建完成")
        return compiled_graph

    async def execute(
        self,
        user_input: str,
        session_id: str = "default"
    ) -> AsyncGenerator[Dict[str, Any], None]:
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
            # 初始化状态
            initial_state: PlanExecuteState = {
                "input": user_input,
                "plan": [],
                "past_steps": [],
                "response": "",
                "steps_summary": "",
            }

            # 流式执行工作流
            config_dict = {
                "configurable": {
                    "thread_id": session_id
                }
            }

            async for event in self.graph.astream(
                input=initial_state,
                config=config_dict,
                stream_mode="updates"
            ):
                # 解析事件
                for node_name, node_output in event.items():
                    logger.info(f"节点 '{node_name}' 输出事件")

                    # 根据节点类型生成不同的事件
                    if node_name == NODE_PLANNER:
                        yield self._format_planner_event(node_output)

                    elif node_name == NODE_EXECUTOR:
                        yield self._format_executor_event(node_output)

                    elif node_name == NODE_MICROCOMPACT:
                        pass  # microcompact 内部已打印 TokenMeter，此处静默

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
            if final_response:
                try:
                    await memory_writer.save_incident(
                        session_id=session_id,
                        task_description=user_input[:120],
                        report=final_response,
                    )
                except Exception as mem_err:
                    logger.warning(f"[MemoryWriter] 保存 incident 失败（不影响主流程）: {mem_err}")

            # 发送完成事件
            yield {
                "type": "complete",
                "stage": "complete",
                "message": "任务执行完成",
                "response": final_response
            }

        except Exception as e:
            logger.error(f"[会话 {session_id}] 任务执行失败: {e}", exc_info=True)
            yield {
                "type": "error",
                "stage": "error",
                "message": f"任务执行出错: {str(e)}"
            }

    async def diagnose(
        self,
        session_id: str = "default"
    ) -> AsyncGenerator[Dict[str, Any], None]:
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
                    "diagnosis": {
                        "status": "completed",
                        "report": event.get("response", "")
                    }
                }
            else:
                yield event

    async def execute_alert_diagnosis(self, alert: "AlertRecord") -> str:  # type: ignore[name-defined]
        """
        根据真实告警 payload 构造明确 prompt，运行 Plan-Execute-Replan 诊断，
        返回最终报告文本。

        Args:
            alert: AlertRecord 实例

        Returns:
            str: 诊断报告 Markdown 文本
        """
        from textwrap import dedent
        from app.models.alert import AlertRecord as _AlertRecord

        recent_pts = ""
        if alert.evidence and alert.evidence.recent_points:
            pts = alert.evidence.recent_points[-10:]
            recent_pts = "\n".join(f"  {p[0]}  {p[1]}" for p in pts)
            recent_pts = f"\n最近数据点（时间, 值）:\n{recent_pts}"

        task = dedent(f"""
            当前主机 [{alert.host}] 发生了一条 [{alert.severity.upper()}] 级别告警：

            告警名称: {alert.alert_name}
            指标:     {alert.metric}
            当前值:   {alert.value}
            阈值:     {alert.threshold}
            持续时间: {alert.duration_sec} 秒
            告警时间: {alert.ts}
            传感器ID: {alert.sensor_id or '未知'}{recent_pts}

            请基于以上真实告警数据，结合知识库经验和可用监控工具，
            分析告警根因并生成完整的诊断报告。报告格式要求同标准 AIOps 报告模板。
        """).strip()

        session_id = f"alert_{alert.alert_id}"
        report = ""
        async for event in self.execute(task, session_id):
            if event.get("type") == "complete":
                report = event.get("response", "")
                break
            if event.get("type") == "report":
                report = event.get("report", "")

        return report or "诊断未返回报告内容"

    def _format_planner_event(self, state: Dict | None) -> Dict:
        """格式化 Planner 节点事件"""
        if not state:
            return {
                "type": "status",
                "stage": "planner",
                "message": "规划节点执行中"
            }

        plan = state.get("plan", [])

        return {
            "type": "plan",
            "stage": "plan_created",
            "message": f"执行计划已制定，共 {len(plan)} 个步骤",
            "plan": plan
        }

    def _format_executor_event(self, state: Dict | None) -> Dict:
        """格式化 Executor 节点事件"""
        if not state:
            return {
                "type": "status",
                "stage": "executor",
                "message": "执行节点运行中"
            }

        plan = state.get("plan", [])
        past_steps = state.get("past_steps", [])

        if past_steps:
            last_step, _ = past_steps[-1]
            return {
                "type": "step_complete",
                "stage": "step_executed",
                "message": f"步骤执行完成 ({len(past_steps)}/{len(past_steps) + len(plan)})",
                "current_step": last_step,
                "remaining_steps": len(plan)
            }
        else:
            return {
                "type": "status",
                "stage": "executor",
                "message": "开始执行步骤"
            }

    def _format_replanner_event(self, state: Dict | None) -> Dict:
        """格式化 Replanner 节点事件"""
        if not state:
            return {
                "type": "status",
                "stage": "replanner",
                "message": "评估节点运行中"
            }

        response = state.get("response", "")
        plan = state.get("plan", [])

        if response:
            # 已生成最终响应
            return {
                "type": "report",
                "stage": "final_report",
                "message": "最终报告已生成",
                "report": response
            }
        else:
            # 重新规划
            return {
                "type": "status",
                "stage": "replanner",
                "message": f"评估完成，{'继续执行剩余步骤' if plan else '准备生成最终响应'}",
                "remaining_steps": len(plan)
            }


# 全局单例
aiops_service = AIOpsService()
