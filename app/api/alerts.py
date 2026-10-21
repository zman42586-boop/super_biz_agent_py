"""告警接收、心跳监控与查询接口"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.config import config
from app.models.alert import AlertIngestRequest, AlertIngestResponse, AlertRecord
from app.services.alert_service import alert_service

router = APIRouter()
_bearer = HTTPBearer(auto_error=True)


def _verify_token(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> None:
    """校验 Webhook Bearer Token。"""
    expected = config.alert_webhook_token.strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务端未配置 ALERT_WEBHOOK_TOKEN",
        )
    if creds.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效",
        )


@router.post(
    "/alerts/ingest",
    response_model=AlertIngestResponse,
    summary="接收本机告警事件（Webhook）",
    dependencies=[Depends(_verify_token)],
)
async def ingest_alert(req: AlertIngestRequest) -> AlertIngestResponse:
    """
    本机 Alert Agent 上报告警事件。

    - 对活跃告警去重：同一告警 ID 已活跃则忽略
    - 首次收到立即后台发送告警邮件
    - critical 告警可选后台触发 AIOps 自动诊断
    """
    record, is_new = await alert_service.ingest(req)
    diagnosis_triggered = False
    diagnosis_run_id = record.diagnosis_run_id

    should_queue_diagnosis = (
        req.severity == "critical"
        and config.oncall_auto_diagnosis
        and record.diagnosis_run_id is None
    )
    if should_queue_diagnosis:
        diagnosis_run_id = await alert_service.trigger_diagnosis(record)
        diagnosis_triggered = diagnosis_run_id is not None

    return AlertIngestResponse(
        alert_id=record.alert_id,
        status="new" if is_new else "duplicate",
        message="告警已接收并处理" if is_new else "告警已存在，忽略重复上报",
        diagnosis_triggered=diagnosis_triggered,
        diagnosis_run_id=diagnosis_run_id,
    )


@router.get(
    "/alerts/active",
    response_model=list[AlertRecord],
    summary="查询当前活跃告警列表",
)
async def list_active_alerts() -> list[AlertRecord]:
    """返回内存中所有 status=active 的告警。"""
    return alert_service.get_active_alerts()


@router.post(
    "/alerts/{alert_id}/resolve",
    response_model=AlertRecord,
    summary="手动标记告警为已恢复",
)
async def resolve_alert(alert_id: str) -> AlertRecord:
    """将指定 alert_id 的告警状态置为 resolved。"""
    record = alert_service.resolve(alert_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到告警 {alert_id}",
        )
    return record


class HeartbeatRequest(BaseModel):
    """Agent 心跳上报：轻量系统指标，日志通过独立接口上传。"""

    host: str = Field(min_length=1, max_length=128, description="主机名，用于标识上报来源")
    recent_points: list = Field(
        default_factory=list, max_length=10, description="旧版指标数据点；新客户端不再通过心跳上传指标"
    )
    cpu_percent: float | None = Field(default=None, description="当前 CPU 使用率 %")
    memory_percent: float | None = Field(default=None, description="当前内存使用率 %")
    top_processes: list = Field(
        default_factory=list, max_length=5, description="TOP 进程列表 [{name, cpu, memory}, ...]"
    )
    critical_snapshot: dict | None = Field(
        default=None, description="旧版系统快照；新客户端在退出事件中附带 Prometheus 查询结果"
    )


@router.post(
    "/heartbeat",
    summary="Agent 心跳上报（含系统快照供死因分析）",
    dependencies=[Depends(_verify_token)],
)
async def heartbeat(req: HeartbeatRequest) -> dict:
    """Agent 进程定期调用此接口告知主服务"我还活着"。

    - 服务端记录每个主机的最近心跳时间和系统快照
    - 超过阈值未收到心跳，持久化失联告警和降级诊断任务
    - 心跳失联不能证明主机已经物理宕机
    - 心跳恢复后自动解除失联告警
    """
    snapshot = req.model_dump(exclude={"host"})
    await alert_service.record_heartbeat(req.host, snapshot)
    return {"status": "ok", "host": req.host}


class KeyLogEntry(BaseModel):
    event_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    source: str = Field(default="MATLAB", max_length=256)
    offset: int = Field(default=0, ge=0)
    ts: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=16384)


class KeyLogRequest(BaseModel):
    host: str = Field(min_length=1, max_length=128)
    entries: list[KeyLogEntry] = Field(max_length=100)


@router.post(
    "/host-logs", dependencies=[Depends(_verify_token)], summary="增量上传 MATLAB 关键日志"
)
async def host_logs(req: KeyLogRequest):
    try:
        await alert_service.record_logs(req.host, [entry.model_dump() for entry in req.entries])
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"status": "ok", "host": req.host}
