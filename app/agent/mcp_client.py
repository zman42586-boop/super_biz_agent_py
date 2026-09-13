"""
MCP 客户端管理
提供全局单例的 MCP 客户端，避免重复初始化
"""

import asyncio
import time
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from loguru import logger
from mcp.types import CallToolResult, TextContent

from app.config import config

# 全局 MCP 客户端（延迟初始化）
_mcp_client: MultiServerMCPClient | None = None


class MCPCircuitOpenError(RuntimeError):
    """MCP 工具发现处于熔断冷却期。"""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        super().__init__(
            f"MCP circuit is open; retry after {self.retry_after_seconds:.1f}s"
        )


class MCPToolProvider:
    """Cache MCP tool schemas and circuit-break repeated discovery failures.

    closed -> open: consecutive failures reach the threshold.
    open -> half-open: cooldown expires; one caller is allowed to probe.
    half-open -> closed/open: probe succeeds/fails.
    """

    def __init__(self, *, failure_threshold: int = 1, cooldown_seconds: float = 30.0):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._tools: list[Any] | None = None
        self._failure_count = 0
        self._opened_at: float | None = None
        self._state = "closed"
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    async def get_tools(self, client_factory) -> list[Any]:
        if self._tools is not None:
            return list(self._tools)

        self._reject_during_cooldown()
        async with self._lock:
            # Another caller may have completed discovery while this caller waited.
            if self._tools is not None:
                return list(self._tools)
            self._reject_during_cooldown()

            if self._state == "open":
                self._state = "half_open"
                logger.info("MCP 熔断器进入 half-open，尝试单次恢复探测")

            try:
                client = await client_factory()
                tools = list(await client.get_tools())
            except Exception:
                self._record_failure()
                raise

            self._tools = tools
            self._failure_count = 0
            self._opened_at = None
            self._state = "closed"
            logger.info(f"MCP 工具发现成功并缓存 {len(tools)} 个 Tool Schema")
            return list(tools)

    def _reject_during_cooldown(self) -> None:
        if self._state != "open" or self._opened_at is None:
            return
        elapsed = time.monotonic() - self._opened_at
        remaining = self.cooldown_seconds - elapsed
        if remaining > 0:
            raise MCPCircuitOpenError(remaining)

    def _record_failure(self) -> None:
        self._failure_count += 1
        if self._state == "half_open" or self._failure_count >= self.failure_threshold:
            self._state = "open"
            self._opened_at = time.monotonic()
            logger.warning(
                f"MCP 熔断器已打开，{self.cooldown_seconds:.1f}s 内快速降级"
            )

    def reset(self) -> None:
        """Clear cached schemas and breaker state; primarily useful for tests/admin."""
        self._tools = None
        self._failure_count = 0
        self._opened_at = None
        self._state = "closed"


async def retry_interceptor(
    request: MCPToolCallRequest,
    handler,
    max_retries: int = 3,
    delay: float = 1.0,
):
    """MCP 工具调用重试拦截器

    当工具调用失败时，使用指数退避策略自动重试。
    如果所有重试都失败，返回包含错误信息的结果而不是抛出异常。

    MCPToolCallRequest 结构：
    - name: str - 工具名称
    - args: dict[str, Any] - 工具参数
    - server_name: str - 服务器名称

    Args:
        request: MCP 工具调用请求
        handler: 实际的工具调用处理器
        max_retries: 最大重试次数（默认3次）
        delay: 初始延迟时间（秒，默认1秒）

    Returns:
        CallToolResult: 工具调用结果或错误信息
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            logger.info(
                f"调用 MCP 工具: {request.name} "
                f"(服务器: {request.server_name}, 第 {attempt + 1}/{max_retries} 次尝试)"
            )
            result = await handler(request)
            logger.info(f"MCP 工具 {request.name} 调用成功")
            return result

        except Exception as e:
            last_error = e
            logger.warning(
                f"MCP 工具 {request.name} 调用失败 "
                f"(第 {attempt + 1}/{max_retries} 次): {str(e)}"
            )

            # 如果不是最后一次尝试，等待后重试
            if attempt < max_retries - 1:
                wait_time = delay * (2 ** attempt)  # 指数退避
                logger.info(f"等待 {wait_time:.1f} 秒后重试...")
                await asyncio.sleep(wait_time)

    # 所有重试都失败，返回错误结果而不是抛出异常
    error_msg = f"工具 {request.name} 在 {max_retries} 次重试后仍然失败: {str(last_error)}"
    logger.error(error_msg)
    return CallToolResult(
        content=[TextContent(type="text", text=error_msg)],
        isError=True
    )


# 使用配置文件中定义的完整 MCP 服务器配置
DEFAULT_MCP_SERVERS = config.mcp_servers
_mcp_tool_provider = MCPToolProvider(
    failure_threshold=config.mcp_circuit_failure_threshold,
    cooldown_seconds=config.mcp_circuit_cooldown_seconds,
)


async def get_mcp_client(
    servers: dict[str, dict[str, str]] | None = None,
    tool_interceptors: list | None = None,
    force_new: bool = False,
) -> MultiServerMCPClient:
    """
    获取或初始化 MCP 客户端（不带重试拦截器）

    这是一个单例模式，确保整个应用只有一个 MCP 客户端实例（除非 force_new=True）

    从 langchain-mcp-adapters 0.1.0 开始，MultiServerMCPClient 不再支持作为上下文管理器使用。
    直接创建实例即可使用。

    Args:
        servers: MCP 服务器配置，默认使用 DEFAULT_MCP_SERVERS
        tool_interceptors: 自定义工具拦截器列表
        force_new: 是否强制创建新实例（用于特殊场景，如需要不同配置）

    Returns:
        MultiServerMCPClient: MCP 客户端实例
    """
    global _mcp_client

    # 如果请求新实例，直接创建并返回（不缓存）
    if force_new:
        logger.info("创建新的 MCP 客户端实例（非单例）")
        client = _create_mcp_client(servers or DEFAULT_MCP_SERVERS, tool_interceptors)
        # 不再需要 __aenter__()，直接返回即可
        return client

    # 单例模式：如果已存在，直接返回
    if _mcp_client is None:
        logger.info("初始化全局 MCP 客户端...")
        _mcp_client = _create_mcp_client(
            servers or DEFAULT_MCP_SERVERS, tool_interceptors
        )
        # 不再需要 __aenter__()，直接使用即可
        logger.info("全局 MCP 客户端初始化完成")

    return _mcp_client


async def get_mcp_client_with_retry(
    servers: dict[str, dict[str, str]] | None = None,
    tool_interceptors: list | None = None,
    force_new: bool = False,
) -> MultiServerMCPClient:
    """
    获取或初始化带重试功能的 MCP 客户端

    这是一个单例模式，确保整个应用只有一个 MCP 客户端实例（除非 force_new=True）
    重试拦截器会自动添加到拦截器列表的开头

    Args:
        servers: MCP 服务器配置，默认使用 DEFAULT_MCP_SERVERS
        tool_interceptors: 自定义工具拦截器列表（会在重试拦截器之后添加）
        force_new: 是否强制创建新实例（用于特殊场景，如需要不同配置）

    Returns:
        MultiServerMCPClient: 带重试功能的 MCP 客户端实例
    """
    # 构建拦截器列表：重试拦截器在最前面
    interceptors = [retry_interceptor]
    if tool_interceptors:
        interceptors.extend(tool_interceptors)

    return await get_mcp_client(
        servers=servers,
        tool_interceptors=interceptors,
        force_new=force_new,
    )


async def get_mcp_tools_with_circuit_breaker() -> list[Any]:
    """Return cached MCP tools or fail fast while discovery is circuit-broken."""

    return await _mcp_tool_provider.get_tools(get_mcp_client_with_retry)


def _create_mcp_client(
    servers: dict[str, dict[str, str]],
    tool_interceptors: list | None = None,
) -> MultiServerMCPClient:
    """
    创建 MCP 客户端实例

    Args:
        servers: MCP 服务器配置
        tool_interceptors: 工具拦截器列表

    Returns:
        MultiServerMCPClient: 未初始化的客户端实例
    """
    # MultiServerMCPClient 的第一个参数直接接收 servers 配置字典
    # 格式: {server_name: {"transport": "...", "url": "..."}}
    kwargs: dict[str, Any] = {}

    if tool_interceptors:
        kwargs["tool_interceptors"] = tool_interceptors

    # 第一个参数是 servers 配置，直接传递
    return MultiServerMCPClient(servers, **kwargs)  # type: ignore[arg-type]
