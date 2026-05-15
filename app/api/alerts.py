"""告警接收、心跳监控与查询接口"""

from __future__ import annotations

from typing import List

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

    if is_new and req.severity == "critical" and config.oncall_auto_diagnosis:
        await alert_service.trigger_diagnosis(record)
        diagnosis_triggered = True

    return AlertIngestResponse(
        alert_id=record.alert_id,
        status="new" if is_new else "duplicate",
        message="告警已接收并处理" if is_new else "告警已存在，忽略重复上报",
        diagnosis_triggered=diagnosis_triggered,
    )


@router.get(
    "/alerts/active",
    response_model=List[AlertRecord],
    summary="查询当前活跃告警列表",
)
async def list_active_alerts() -> List[AlertRecord]:
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
    """边缘 Agent 心跳上报请求体——携带系统状态供死因分析"""
    host: str = Field(description="主机名，用于标识上报来源")
    temperature: float | None = Field(default=None, description="当前温度 (°C)")
    temp_threshold: float | None = Field(default=None, description="告警阈值 (°C)")
    recent_points: list = Field(default_factory=list, description="最近温度数据点 [[ts, val], ...]")
    critical_snapshot: dict | None = Field(default=None, description="温度危险时的系统快照 (CPU/内存/进程)")


@router.post(
    "/heartbeat",
    summary="边缘 Agent 心跳上报（含系统快照供死因分析）",
    dependencies=[Depends(_verify_token)],
)
async def heartbeat(req: HeartbeatRequest) -> dict:
    """边缘 Agent 定期调用此接口告知云端"我还活着"。

    - 云端记录每个主机的最近心跳时间和系统快照
    - 超过 oncall_heartbeat_timeout_sec 秒未收到心跳 → 自动宕机告警
    - 宕机告警会包含最后心跳的温度/CPU/进程数据，用于推断死因
    - 心跳恢复后自动 resolve 宕机告警
    """
    snapshot = req.model_dump(exclude={"host"})
    alert_service.record_heartbeat(req.host, snapshot)
    return {"status": "ok", "host": req.host}
