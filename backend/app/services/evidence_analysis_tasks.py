"""Process-local task lifecycle for asynchronous V2 evidence analysis."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from uuid import uuid4

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.report_v2 import EvidenceFirstReport
from app.services.evidence_analysis_service import (
    EvidenceAnalysisResult,
    EvidenceAnalysisService,
    EvidenceAnalysisServiceError,
)

logger = logging.getLogger(__name__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceAnalysisCreateRequest(StrictModel):
    enterprise_name: str


class EvidenceAnalysisCreateResponse(StrictModel):
    task_id: str
    status: Literal["processing"] = "processing"


class EvidenceAnalysisTaskError(StrictModel):
    code: str
    category: str | None
    stage: str
    message: str


class EvidenceAnalysisTaskResponse(StrictModel):
    task_id: str
    status: Literal["processing", "completed", "failed"]
    current_stage: Literal["queued", "research", "technology_semantic", "technology_finance", "evidence_assertions", "evidence_first_report", "complete", "failed"]
    enterprise_name: str
    result_status: Literal["completed", "entity_not_resolved"] | None = None
    company_id: str | None = None
    canonical_name: str | None = None
    technology_profile_id: str | None = None
    finance_profile_id: str | None = None
    assertion_profile_id: str | None = None
    report: EvidenceFirstReport | None = None
    report_url: str | None = None
    error: EvidenceAnalysisTaskError | None = None


class EvidenceAnalysisTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, EvidenceAnalysisTaskResponse] = {}
        self._lock = asyncio.Lock()

    async def create(self, task_id: str, enterprise_name: str) -> EvidenceAnalysisTaskResponse:
        record = EvidenceAnalysisTaskResponse(
            task_id=task_id, status="processing", current_stage="queued",
            enterprise_name=enterprise_name,
        )
        async with self._lock:
            self._tasks[task_id] = record
        return record.model_copy(deep=True)

    async def get(self, task_id: str) -> EvidenceAnalysisTaskResponse | None:
        async with self._lock:
            record = self._tasks.get(task_id)
            return record.model_copy(deep=True) if record else None

    async def progress(self, task_id: str, stage: str) -> None:
        async with self._lock:
            record = self._require(task_id)
            self._tasks[task_id] = record.model_copy(update={"current_stage": stage})

    async def complete(self, task_id: str, result: EvidenceAnalysisResult) -> None:
        report_url = f"/api/report/v2/company/{result.company_id}" if result.company_id and result.report else None
        async with self._lock:
            record = self._require(task_id)
            self._tasks[task_id] = record.model_copy(update={
                "status": "completed", "current_stage": "complete",
                "result_status": result.result_status, "company_id": result.company_id,
                "canonical_name": result.canonical_name,
                "technology_profile_id": result.technology_profile_id,
                "finance_profile_id": result.finance_profile_id,
                "assertion_profile_id": result.assertion_profile_id,
                "report": result.report, "report_url": report_url, "error": None,
            }, deep=True)

    async def fail(self, task_id: str, error: EvidenceAnalysisTaskError) -> None:
        async with self._lock:
            record = self._require(task_id)
            self._tasks[task_id] = record.model_copy(update={
                "status": "failed", "current_stage": "failed", "report": None, "error": error,
            }, deep=True)

    def _require(self, task_id: str) -> EvidenceAnalysisTaskResponse:
        if task_id not in self._tasks:
            raise KeyError("task_not_found")
        return self._tasks[task_id]


class EvidenceAnalysisTaskManager:
    def __init__(self, service: EvidenceAnalysisService, store: EvidenceAnalysisTaskStore | None = None) -> None:
        self.service = service
        self.store = store or EvidenceAnalysisTaskStore()

    @staticmethod
    def new_task_id() -> str:
        return uuid4().hex

    async def create(self, task_id: str, enterprise_name: str) -> EvidenceAnalysisTaskResponse:
        return await self.store.create(task_id, enterprise_name)

    async def get(self, task_id: str) -> EvidenceAnalysisTaskResponse | None:
        return await self.store.get(task_id)

    async def process(self, task_id: str, enterprise_name: str) -> None:
        async def progress_callback(stage: str, metadata: dict) -> None:
            await self.store.progress(task_id, stage)

        try:
            result = await self.service.analyze_company(enterprise_name, progress_callback=progress_callback)
            await self.store.complete(task_id, result)
        except EvidenceAnalysisServiceError as exc:
            logger.warning("Evidence analysis task %s failed at %s (%s)", task_id, exc.stage, exc.category or exc.code)
            await self.store.fail(task_id, EvidenceAnalysisTaskError(
                code=exc.code, category=exc.category, stage=exc.stage, message=exc.message,
            ))
        except Exception as exc:
            category = type(exc).__name__
            logger.warning("Evidence analysis task %s failed unexpectedly (%s)", task_id, category)
            await self.store.fail(task_id, EvidenceAnalysisTaskError(
                code="analysis_failed", category=category, stage="failed",
                message="分析流程执行失败，请稍后重试。",
            ))


@lru_cache
def get_evidence_analysis_task_manager() -> EvidenceAnalysisTaskManager:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "runtime" / "knowledge"
    return EvidenceAnalysisTaskManager(EvidenceAnalysisService(runtime_root=root))
