"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

from typing import Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "SuperBizAgent"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900

    # LLM 配置（当前使用 DeepSeek，OpenAI 兼容模式）
    dashscope_api_key: str = ""
    dashscope_api_base: str = "https://api.deepseek.com/v1"
    dashscope_model: str = "deepseek-chat"
    dashscope_embedding_model: str = "BAAI/bge-small-zh-v1.5"

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 3
    rag_model: str = "deepseek-chat"

    # 文档分块配置
    chunk_max_size: int = 800
    chunk_overlap: int = 100

    # MCP 服务配置（支持多 Server，本地 + 远程均可）
    # Monitor MCP — 本机监控数据采集（CPU / 内存 / LHM 温度）
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"
    # CLS MCP — 腾讯云日志服务（远程），参考: https://cloud.tencent.com/developer/mcp/server/11710
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    # 预留：通用远程 MCP Server，方便快速接入公网工具
    mcp_remote_transport: str = "streamable-http"
    mcp_remote_url: str = ""

    # SMTP 邮件配置
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

    # 告警 Webhook 配置
    alert_webhook_token: str = ""

    # LibreHardwareMonitor 配置
    lhm_base_url: str = "http://127.0.0.1:8085"
    # LHM 温度传感器名称子串（AMD 等多为 CCDs Max (Tdie)，非 Intel 的 CPU Package）
    lhm_sensor_name_contains: str = "CCDs Max (Tdie)"

    # OnCall 告警规则（温度，用于本机 Agent）
    oncall_temp_threshold_c: float = 50.0     # 触发温度（°C）
    oncall_temp_duration_sec: int = 60        # 连续超阈持续秒数
    oncall_temp_cooldown_sec: int = 120       # 告警冷却期（秒），期间不重复发邮件
    # 心跳超时：Agent 定期上报心跳，超过此时间未收到则判定主机失联
    oncall_heartbeat_timeout_sec: int = 60

    # AIOps 自动诊断开关（仅对 critical 告警）
    oncall_auto_diagnosis: bool = True

    @property
    def mcp_servers(self) -> Dict[str, Dict[str, Any]]:
        """获取完整的 MCP 服务器配置（自动跳过未配置 URL 的 Server）"""
        servers: Dict[str, Dict[str, Any]] = {
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            },
        }
        # CLS 日志 MCP（远程） — 仅当配置了 URL 才注册
        if self.mcp_cls_url.strip():
            servers["cls"] = {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            }
        # 通用远程 MCP — 快速接入任意公网工具
        if self.mcp_remote_url.strip():
            servers["remote"] = {
                "transport": self.mcp_remote_transport,
                "url": self.mcp_remote_url,
            }
        return servers


# 全局配置实例
config = Settings()
