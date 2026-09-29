"""长期记忆人工审核接口。"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.memory import memory_writer

router = APIRouter()


class HumanReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=1000)


@router.get("/memory/reviews")
async def list_memory_reviews(
    status: Literal["approved", "pending_review", "rejected"] | None = Query(default=None),
):
    """查看待人工审核及历史审核记录。"""
    return {"items": memory_writer.list_reviews(status=status)}


@router.post("/memory/reviews/{incident_name}")
async def review_memory(incident_name: str, request: HumanReviewRequest):
    """人工批准或拒绝 Incident；批准后才会写入 Milvus。"""
    try:
        record = await memory_writer.human_review(
            incident_name=incident_name,
            decision=request.decision,
            reviewer=request.reviewer,
            note=request.note,
        )
        return {"message": "review_saved", "data": record}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Incident 或审核记录不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
