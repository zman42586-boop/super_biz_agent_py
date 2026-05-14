"""告警接收与查询接口"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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
