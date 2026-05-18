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

    # MCP 服务配置 — 本机监控数据采集（CPU / 内存 / LHM 温度）
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

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
    oncall_temp_enabled: bool = False         # 温度监控开关（默认关闭，主要靠进程监控）
    oncall_temp_threshold_c: float = 85.0     # 触发温度（°C）
    oncall_temp_duration_sec: int = 60        # 连续超阈持续秒数
    oncall_temp_cooldown_sec: int = 120       # 告警冷却期（秒），期间不重复发邮件
    # 心跳超时：Agent 定期上报心跳，超过此时间未收到则判定主机失联
    oncall_heartbeat_timeout_sec: int = 60

    # AIOps 自动诊断开关（仅对 critical 告警）
    oncall_auto_diagnosis: bool = True

    # 进程监控配置（检测 MATLAB 等长时间运行进程的崩溃）
    oncall_monitor_process: str = ""        # 监控的进程名，如 MATLAB.exe
    oncall_monitor_crash_dir: str = ""      # 崩溃日志目录
    oncall_monitor_crash_pattern: str = ""  # 崩溃日志文件名模式，如 matlab_crash_dump.*

    @property
    def mcp_servers(self) -> Dict[str, Dict[str, Any]]:
        """获取 MCP 服务器配置（当前仅本机 Monitor MCP）"""
        return {
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            },
        }


# 全局配置实例
config = Settings()
