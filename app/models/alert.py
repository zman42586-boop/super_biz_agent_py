"""告警数据模型"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AlertEvidence(BaseModel):
    """告警证据（传感器最近数据点、崩溃日志等）"""
    lhm_url: str | None = None
    recent_points: list[list[Any]] = Field(default_factory=list)
    crash_log: str | None = Field(default=None, description="进程崩溃日志摘要")
    crash_type: str | None = Field(default=None, description="崩溃类型: stack_overflow / access_violation 等")
    monitored_process: str | None = Field(default=None, description="被监控的进程名")
    system_snapshot: dict | None = Field(default=None, description="崩溃时系统快照 {cpu, memory, top_processes}")
    extra: dict[str, Any] = Field(default_factory=dict)


class AlertIngestRequest(BaseModel):
    """本机 Agent 上报的告警 webhook 请求体"""
    source: str = Field(description="数据来源，如 librehardwaremonitor")
    host: str = Field(description="发生告警的主机名")
    severity: str = Field(description="严重等级：warning / critical")
    alert_name: str = Field(description="告警名称，如 CPU Package Temperature High")
    metric: str = Field(description="指标名，如 temperature_c")
    value: float = Field(description="当前值")
    threshold: float = Field(description="触发阈值")
    duration_sec: int = Field(description="持续超阈值秒数")
    sensor_id: str = Field(default="", description="传感器 ID")
    ts: str = Field(description="告警时间（ISO 8601）")
    evidence: AlertEvidence = Field(default_factory=AlertEvidence)


class AlertRecord(BaseModel):
    """内存中存储的活跃告警记录"""
    alert_id: str
    source: str
    host: str
    severity: str
    alert_name: str
    metric: str
    value: float
    threshold: float
    duration_sec: int
    sensor_id: str
    ts: str
    evidence: AlertEvidence
    received_at: datetime = Field(default_factory=datetime.now)
    status: str = "active"      # active / resolved
    diagnosis_report: str | None = None
    diagnosis_run_id: str | None = None


class AlertIngestResponse(BaseModel):
    """告警接收响应"""
    alert_id: str
    status: str
    message: str
    diagnosis_triggered: bool = False
    diagnosis_run_id: str | None = None
