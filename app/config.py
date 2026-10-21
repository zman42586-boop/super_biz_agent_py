"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

from typing import Any

from pydantic import Field, model_validator
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
    dashscope_model: str = "deepseek-v4-flash"
    eval_judge_model: str = "deepseek-v4-pro"
    dashscope_embedding_model: str = "BAAI/bge-small-zh-v1.5"

    # 长期记忆入库门禁：高分自动通过，中间分/模糊/高风险转人工审核
    memory_auto_approve_score: float = 0.80
    memory_reject_score: float = 0.45

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 5
    rag_model: str = "deepseek-v4-flash"
    rag_dense_candidates: int = 20
    rag_sparse_candidates: int = 20
    rag_rerank_candidates: int = 20
    rag_rrf_k: int = 60
    rag_reranker_model: str = "BAAI/bge-reranker-base"
    rag_reranker_enabled: bool = True
    rag_reranker_weight: float = 0.20
    rag_confidence_rank_window: int = 10
    rag_semantic_evidence_threshold: float = 0.55
    rag_retry_on_low_confidence: bool = True

    # 文档分块配置
    chunk_max_tokens: int = 420
    chunk_overlap_tokens: int = 64

    # MCP 是 Prometheus 的只读适配器，不采集所在机器的指标。
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"
    prometheus_url: str = "http://127.0.0.1:9090"
    prometheus_bearer_token: str = ""
    prometheus_timeout_sec: float = Field(default=5, gt=0, le=10)
    prometheus_max_range_sec: int = Field(default=7200, ge=1, le=86400)
    prometheus_max_points: int = Field(default=240, ge=2, le=1000)
    prometheus_freshness_sec: int = Field(default=60, ge=5, le=300)
    # MCP 工具发现失败后短路降级，避免 Planner/Executor/Replanner 重复等待
    mcp_circuit_failure_threshold: int = 1
    mcp_circuit_cooldown_seconds: float = 30.0

    # SMTP 邮件配置
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

    # 告警 Webhook 配置
    alert_webhook_token: str = ""

    # 心跳超时：Agent 定期上报心跳，超过此时间未收到则判定主机失联
    oncall_heartbeat_timeout_sec: int = Field(default=60, ge=1)
    oncall_heartbeat_check_interval_sec: float = Field(default=10, gt=0)
    oncall_heartbeat_suspected_sec: int = Field(default=30, ge=1)
    oncall_history_ttl_sec: int = Field(default=7200, ge=1)
    oncall_history_metrics_per_host: int = Field(default=1600, ge=1, le=10000)
    oncall_history_logs_per_host: int = Field(default=400, ge=1, le=10000)
    oncall_history_payload_bytes: int = Field(default=65536, ge=1024, le=1048576)
    oncall_history_log_chars: int = Field(default=4096, ge=128, le=16384)

    @model_validator(mode="after")
    def validate_heartbeat_thresholds(self):
        if self.oncall_heartbeat_suspected_sec > self.oncall_heartbeat_timeout_sec:
            raise ValueError("suspected threshold must not exceed down threshold")
        return self

    # AIOps 自动诊断开关（仅对 critical 告警）
    oncall_auto_diagnosis: bool = True

    # 进程监控配置（检测 MATLAB 等长时间运行进程的崩溃）
    oncall_monitor_process: str = ""  # 监控的进程名，如 MATLAB.exe
    oncall_monitor_crash_dir: str = ""  # 崩溃日志目录
    oncall_monitor_crash_pattern: str = ""  # 崩溃日志文件名模式，如 matlab_crash_dump.*

    @property
    def mcp_servers(self) -> dict[str, dict[str, Any]]:
        """获取 Prometheus MCP 地址；Prometheus 数据源由 MCP 进程配置。"""
        return {
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            },
        }


# 全局配置实例
config = Settings()
