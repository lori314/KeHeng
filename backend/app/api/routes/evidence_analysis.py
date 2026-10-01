"""Public task API for the V2 evidence-first company workflow."""

import unicodedata
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.services.evidence_analysis_service import EvidenceAnalysisServiceError
from app.services.evidence_analysis_tasks import (
    EvidenceAnalysisCreateRequest,
    EvidenceAnalysisCreateResponse,
    EvidenceAnalysisTaskManager,
    EvidenceAnalysisTaskResponse,
    get_evidence_analysis_task_manager,
)

router = APIRouter(prefix="/api/evidence-analysis", tags=["evidence-analysis"])


@router.post("", response_model=EvidenceAnalysisCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_evidence_analysis(
    request: EvidenceAnalysisCreateRequest,
    background_tasks: BackgroundTasks,
    manager: Annotated[EvidenceAnalysisTaskManager, Depends(get_evidence_analysis_task_manager)],
) -> EvidenceAnalysisCreateResponse:
    enterprise_name = " ".join(unicodedata.normalize("NFKC", request.enterprise_name).split())
    if not enterprise_name:
        raise HTTPException(status_code=422, detail={"code": "blank_enterprise_name", "message": "企业名称不能为空。"})
    if len(enterprise_name) > 300:
        raise HTTPException(status_code=422, detail={"code": "invalid_enterprise_name", "message": "企业名称长度不能超过 300 个字符。"})
    try:
        manager.service.require_providers()
    except EvidenceAnalysisServiceError as exc:
        raise HTTPException(status_code=503, detail={
            "code": exc.code, "missing_providers": exc.missing_providers,
            "message": exc.message,
        }) from exc
    task_id = manager.new_task_id()
    record = await manager.create(task_id, enterprise_name)
    background_tasks.add_task(manager.process, task_id, enterprise_name)
    return EvidenceAnalysisCreateResponse(task_id=record.task_id, status="processing")


@router.get("/{task_id}", response_model=EvidenceAnalysisTaskResponse)
async def get_evidence_analysis(
    task_id: str,
    manager: Annotated[EvidenceAnalysisTaskManager, Depends(get_evidence_analysis_task_manager)],
) -> EvidenceAnalysisTaskResponse:
    record = await manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "task_not_found", "message": "分析任务不存在。"})
    return record
