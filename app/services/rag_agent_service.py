"""RAG Agent 服务 - 基于 LangGraph 的智能代理

使用 ChatOpenAI (OpenAI 兼容模式) 支持多模型提供商。
"""

from typing import Annotated, Any, AsyncGenerator, Dict, Sequence

from langchain.agents import create_agent
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from loguru import logger
from typing_extensions import TypedDict
from langchain_openai import ChatOpenAI

from app.config import config
from app.tools import get_current_time, retrieve_knowledge, search_log
from app.agent.mcp_client import get_mcp_tools_with_circuit_breaker
from app.core.llm_factory import llm_factory
from app.memory import load_memory_context
from app.utils.token_meter import count_state_tokens, log_compression

# Level 1 Snip：超过此 token 估算值时触发裁剪（演示模式：300 tokens ≈ 1200 字符，几轮对话后触发）
_SNIP_TOKEN_THRESHOLD = 300
# 裁剪后保留最近的非系统消息条数
_SNIP_KEEP_RECENT = 6


def _unwrap_exception(exc: Exception) -> str:
    """递归提取 ExceptionGroup 中的子异常，返回完整错误信息"""
    if isinstance(exc, BaseExceptionGroup):
        sub_errors = []
        for sub in exc.exceptions:
            sub_errors.append(_unwrap_exception(sub))
        return "; ".join(sub_errors)
    return f"{type(exc).__name__}: {exc}"


class AgentState(TypedDict):
    """Agent 状态"""
    messages: Annotated[Sequence[BaseMessage], add_messages]


class RagAgentService:
    """RAG Agent 服务 - 使用 LangGraph + ChatOpenAI"""

    def __init__(self, streaming: bool = True):
        """初始化 RAG Agent 服务

        Args:
            streaming: 是否启用流式输出，默认为 True
        """
        self.model_name = config.rag_model
        self.streaming = streaming
        self.system_prompt = self._build_system_prompt()


        self.model = llm_factory.create_chat_model(
            model=self.model_name,
            temperature=0.7,
            streaming=streaming,
        )

        # 定义基础工具
        self.tools = [retrieve_knowledge, get_current_time, search_log]

        # MCP 客户端（延迟初始化，使用全局管理）
        self.mcp_tools: list = []

        # 创建内存检查点（用于会话管理）
        self.checkpointer = MemorySaver()

        # Agent 初始化（会在异步方法中完成）
        self.agent = None
        self._agent_initialized = False

        logger.info(f"RAG Agent 服务初始化完成 (ChatOpenAI), model={self.model_name}, streaming={streaming}")

    async def _apply_snip(self, session_id: str) -> None:
        """Level 1 Snip：当会话历史超过 token 阈值时，裁剪旧消息。

        策略：保留第一条系统消息 + 最近 _SNIP_KEEP_RECENT 条非系统消息。
        通过 LangGraph 的 aupdate_state 直接修改检查点，下次调用时生效。
        """
        if self.agent is None:
            return

        config_dict = {"configurable": {"thread_id": session_id}}
        state = self.agent.get_state(config_dict)
        if not state or not state.values:
            return

        messages = list(state.values.get("messages", []))
        if not messages:
            return

        before_tokens = count_state_tokens(messages)
        if before_tokens <= _SNIP_TOKEN_THRESHOLD:
            return

        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        non_system = [m for m in messages if not isinstance(m, SystemMessage)]
        recent = non_system[-_SNIP_KEEP_RECENT:]
        new_messages = system_msgs[:1] + recent

        after_tokens = count_state_tokens(new_messages)
        log_compression("Snip", before_tokens, after_tokens)

        await self.agent.aupdate_state(
            config_dict,
            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new_messages]},
        )

    async def _initialize_agent(self):
        """异步初始化 Agent（包括 MCP 工具，MCP 不可用时降级为仅本地工具）"""
        if self._agent_initialized:
            return

        # 尝试加载 MCP 工具，失败时降级为仅本地工具
        try:
            self.mcp_tools = await get_mcp_tools_with_circuit_breaker()
            logger.info(f"成功加载 {len(self.mcp_tools)} 个 MCP 工具")
        except Exception as e:
            self.mcp_tools = []
            logger.warning(f"MCP 工具加载失败（将仅使用本地工具）: {_unwrap_exception(e)}")

        all_tools = self.tools + self.mcp_tools

        self.agent = create_agent(
            self.model,
            tools=all_tools,
            checkpointer=self.checkpointer,
        )

        self._agent_initialized = True

        if all_tools:
            tool_names = [tool.name if hasattr(tool, "name") else str(tool) for tool in all_tools]
            logger.info(f"可用工具列表: {', '.join(tool_names)}")

    def _build_system_prompt(self) -> str:
        """
        构建系统提示词

        注意：LangChain 框架会自动将工具信息传递给 LLM，
        因此系统提示词中无需列举具体的工具列表。

        Returns:
            str: 系统提示词
        """
        from textwrap import dedent

        return dedent("""
            你是一个专业的AI助手，能够使用多种工具来帮助用户解决问题。

            工作原则:
            1. 理解用户需求，选择合适的工具来完成任务
            2. 当需要获取实时信息或专业知识时，主动使用相关工具
            3. 基于工具返回的结果提供准确、专业的回答
            4. 如果工具无法提供足够信息，请诚实地告知用户

            回答要求:
            - 保持友好、专业的语气
            - 回答简洁明了，重点突出
            - 基于事实，不编造信息
            - 如有不确定的地方，明确说明

            请根据用户的问题，灵活使用可用工具，提供高质量的帮助。
        """).strip()

    async def query(
        self,
        question: str,
        session_id: str,
    ) -> str:
        """
        非流式处理用户问题（一次性返回完整答案）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Returns:
            str: 完整答案
        """
        try:
            await self._initialize_agent()
            await self._apply_snip(session_id)  # Level 1 Snip

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（非流式）: {question}")

            # 构建系统提示：基础 prompt + 最新长期记忆
            memory_ctx = load_memory_context()
            if memory_ctx:
                system_content = (
                    f"{self.system_prompt}\n\n"
                    f"## 长期记忆参考\n\n"
                    f"{memory_ctx}"
                )
            else:
                system_content = self.system_prompt

            # 构建消息列表（系统提示 + 用户问题）
            messages = [
                SystemMessage(content=system_content),
                HumanMessage(content=question)
            ]

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {
                "configurable": {
                    "thread_id": session_id
                }
            }

            result = await self.agent.ainvoke(
                input=agent_input,
                config=config_dict,
            )

            # 提取最终答案
            messages_result = result.get("messages", [])
            if messages_result:
                last_message = messages_result[-1]
                answer = last_message.content if hasattr(last_message, 'content') else str(last_message)

                # 记录工具调用
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_names = [tc.get("name", "unknown") for tc in last_message.tool_calls]
                    logger.info(f"[会话 {session_id}] Agent 调用了工具: {tool_names}")

                logger.info(f"[会话 {session_id}] RAG Agent 查询完成（非流式）")
                return answer

            logger.warning(f"[会话 {session_id}] Agent 返回结果为空")
            return ""

        except Exception as e:
            detail = _unwrap_exception(e)
            logger.error(f"[会话 {session_id}] RAG Agent 查询失败（非流式）: {detail}")
            raise

    async def query_stream(
        self,
        question: str,
        session_id: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式处理用户问题（逐步返回答案片段）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）

        Yields:
            Dict[str, Any]: 包含流式数据的字典
                - type: "content" | "tool_call" | "complete" | "error"
                - data: 具体内容
        """
        try:
            await self._initialize_agent()
            await self._apply_snip(session_id)  # Level 1 Snip

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（流式）: {question}")

            # 构建系统提示：基础 prompt + 最新长期记忆
            memory_ctx = load_memory_context()
            if memory_ctx:
                system_content = (
                    f"{self.system_prompt}\n\n"
                    f"## 长期记忆参考\n\n"
                    f"{memory_ctx}"
                )
            else:
                system_content = self.system_prompt

            # 构建消息列表（系统提示 + 用户问题）
            messages = [
                SystemMessage(content=system_content),
                HumanMessage(content=question)
            ]

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {
                "configurable": {
                    "thread_id": session_id
                }
            }

            async for token, metadata in self.agent.astream(
                input=agent_input,
                config=config_dict,
                stream_mode="messages",
            ):
                node_name = metadata.get('langgraph_node', 'unknown') if isinstance(metadata, dict) else 'unknown'
                message_type = type(token).__name__

                if message_type in ("AIMessage", "AIMessageChunk"):
                    content_blocks = getattr(token, 'content_blocks', None)

                    if content_blocks and isinstance(content_blocks, list):
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                text_content = block.get('text', '')
                                if text_content:
                                    yield {
                                        "type": "content",
                                        "data": text_content,
                                        "node": node_name
                                    }

            logger.info(f"[会话 {session_id}] RAG Agent 查询完成（流式）")
            yield {"type": "complete"}

        except Exception as e:
            detail = _unwrap_exception(e)
            logger.error(f"[会话 {session_id}] RAG Agent 查询失败（流式）: {detail}")
            yield {
                "type": "error",
                "data": detail
            }
            raise

    def get_session_history(self, session_id: str) -> list:
        """
        获取会话历史（从 MemorySaver checkpointer 中读取）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            list: 消息历史列表 [{"role": "user|assistant", "content": "...", "timestamp": "..."}]
        """
        try:
            # 使用 checkpointer 的 get 方法获取最新的检查点
            config = {"configurable": {"thread_id": session_id}}
            
            # 获取该 thread 的最新检查点
            checkpoint_tuple = self.checkpointer.get(config)
            
            if not checkpoint_tuple:
                logger.info(f"获取会话历史: {session_id}, 消息数量: 0")
                return []
            
            # checkpoint_tuple 可能是命名元组或普通元组，安全地提取 checkpoint
            # 通常第一个元素是 checkpoint 数据
            if hasattr(checkpoint_tuple, 'checkpoint'):
                checkpoint_data = checkpoint_tuple.checkpoint  # type: ignore
            else:
                # 如果是普通元组，第一个元素是 checkpoint
                checkpoint_data = checkpoint_tuple[0] if checkpoint_tuple else {}
            
            # 从检查点中提取消息
            messages = checkpoint_data.get("channel_values", {}).get("messages", [])
            
            # 转换为前端需要的格式
            history = []
            for msg in messages:
                # 跳过系统消息
                if isinstance(msg, SystemMessage):
                    continue
                    
                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                content = msg.content if hasattr(msg, 'content') else str(msg)
                
                # 提取时间戳（如果有的话）
                timestamp = getattr(msg, 'timestamp', None)
                if timestamp:
                    history.append({
                        "role": role,
                        "content": content,
                        "timestamp": timestamp
                    })
                else:
                    from datetime import datetime
                    history.append({
                        "role": role,
                        "content": content,
                        "timestamp": datetime.now().isoformat()
                    })
            
            logger.info(f"获取会话历史: {session_id}, 消息数量: {len(history)}")
            return history
            
        except Exception as e:
            logger.error(f"获取会话历史失败: {session_id}, 错误: {e}")
            return []

    def clear_session(self, session_id: str) -> bool:
        """
        清空会话历史（从 MemorySaver checkpointer 中删除）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            bool: 是否成功
        """
        try:
            # 使用 checkpointer 的 delete_thread 方法删除该 thread 的所有检查点
            self.checkpointer.delete_thread(session_id)
            
            logger.info(f"已清除会话历史: {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"清空会话历史失败: {session_id}, 错误: {e}")
            return False

    async def cleanup(self):
        """清理资源"""
        try:
            logger.info("清理 RAG Agent 服务资源...")
            # MCP 客户端由全局管理器统一管理，无需手动清理
            logger.info("RAG Agent 服务资源已清理")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")


# 全局单例 - 启用流式输出
rag_agent_service = RagAgentService(streaming=True)
