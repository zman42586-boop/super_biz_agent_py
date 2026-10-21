"""JSON-compatible evidence, claims and verification contracts."""

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    evidence_id: str
    kind: Literal["incident_fact", "knowledge", "model_inference"]
    source_type: str
    source: str
    locator: str
    text: str
    content_hash: str
    run_id: str = ""
    host_id: str = ""
    event_time: str | None = None
    received_at: str | None = None
    document_id: str | None = None
    document_version: str | None = None
    parent_id: str | None = None
    chunk_id: str | None = None
    # Collection time is not the timestamp embedded in a runtime log.
    time_basis: Literal["observed", "received", "unknown"] = "unknown"


class Citation(BaseModel):
    evidence_id: str
    quote: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class DiagnosisClaim(BaseModel):
    text: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)


class CandidateDiagnosis(BaseModel):
    root_cause: str | None = Field(default=None, description="根因未确定时为 null；确定时必须与某条 claim.text 完全一致")
    claims: list[DiagnosisClaim] = Field(default_factory=list, description="所有将写入报告的分析结论及逐条原文引用")
    recommendations: list[str] = Field(default_factory=list, description="待人工执行的建议，不能描述成已执行")
    missing_evidence: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    status: Literal["SUPPORTED", "NON_SUPPORTED"]
    reason: str
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
