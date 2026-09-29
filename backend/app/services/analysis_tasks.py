"""In-memory task lifecycle for the v0.5 single-process demo."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.services.analysis_service import (
    PROJECT_ROOT,
    AnalysisServiceError,
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)


logger = logging.getLogger(__name__)


class AnalysisTaskStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class AnalysisTaskError(BaseModel):
    code: str
    message: str


class AnalysisTaskResponse(BaseModel):
    """Public polling contract returned by ``GET /analysis/{task_id}``."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: AnalysisTaskStatus
    enterprise_name: str
    file_name: str
    report: object | None = None
    modules: dict = Field(default_factory=dict)
    module_failures: dict = Field(default_factory=dict)
    result_status: str | None = None
    run_info: dict = Field(default_factory=dict)
    model_io: dict = Field(default_factory=dict)
    request_mode: str = "rule_demo"
    error: AnalysisTaskError | None = None


class AnalysisCreateResponse(BaseModel):
    task_id: str
    status: AnalysisTaskStatus


class InMemoryAnalysisTaskStore:
    """Small, process-local store; persistence is intentionally out of v0.5 scope."""

    def __init__(self) -> None:
        self._tasks: dict[str, AnalysisTaskResponse] = {}
        self._lock = asyncio.Lock()

    async def create(
        self, task_id: str, enterprise_name: str, file_name: str, request_mode: str = "rule_demo"
    ) -> AnalysisTaskResponse:
        record = AnalysisTaskResponse(
            task_id=task_id,
            status=AnalysisTaskStatus.PROCESSING,
            enterprise_name=enterprise_name,
            file_name=file_name,
            request_mode=request_mode,
        )
        async with self._lock:
            if task_id in self._tasks:
                raise ValueError(f"Duplicate task_id: {task_id}")
            self._tasks[task_id] = record
        return record.model_copy(deep=True)

    async def get(self, task_id: str) -> AnalysisTaskResponse | None:
        async with self._lock:
            record = self._tasks.get(task_id)
            return record.model_copy(deep=True) if record else None

    async def complete(
        self, task_id: str, product_result: dict
    ) -> AnalysisTaskResponse:
        async with self._lock:
            record = self._require(task_id)
            completed = record.model_copy(
                update={
                    "status": AnalysisTaskStatus.PARTIAL if product_result.get("result_status") == "partial" else AnalysisTaskStatus.FAILED if product_result.get("result_status") == "failed" else AnalysisTaskStatus.COMPLETED,
                    "report": product_result.get("report"),
                    "modules": product_result.get("modules", {}),
                    "module_failures": product_result.get("module_failures", {}),
                    "result_status": product_result.get("result_status", "completed"),
                    "run_info": product_result.get("run_info", {}),
                    "model_io": product_result.get("model_io", {}),
                    "error": None,
                },
                deep=True,
            )
            self._tasks[task_id] = completed
            return completed.model_copy(deep=True)

    async def fail(
        self, task_id: str, error: AnalysisTaskError
    ) -> AnalysisTaskResponse:
        async with self._lock:
            record = self._require(task_id)
            failed = record.model_copy(
                update={
                    "status": AnalysisTaskStatus.FAILED,
                    "report": None,
                    "error": error,
                },
                deep=True,
            )
            self._tasks[task_id] = failed
            return failed.model_copy(deep=True)

    def _require(self, task_id: str) -> AnalysisTaskResponse:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"Unknown analysis task: {task_id}") from exc


class AnalysisTaskManager:
    """Reserve tasks, expose safe upload paths, and execute the unified service."""

    def __init__(
        self,
        service: TechnologyAssessmentService,
        upload_root: str | Path,
        store: InMemoryAnalysisTaskStore | None = None,
    ) -> None:
        self._service = service
        self._upload_root = Path(upload_root).resolve()
        self._store = store or InMemoryAnalysisTaskStore()

    def new_task_id(self) -> str:
        return uuid4().hex

    def upload_path(self, task_id: str) -> Path:
        if not task_id or any(character not in "0123456789abcdef" for character in task_id):
            raise ValueError("Invalid task_id")
        path = (self._upload_root / task_id / "source.pdf").resolve()
        if self._upload_root not in path.parents:
            raise ValueError("Upload path escaped the configured root")
        return path

    async def create(
        self, task_id: str, enterprise_name: str, file_name: str, request_mode: str = "rule_demo"
    ) -> AnalysisTaskResponse:
        return await self._store.create(task_id, enterprise_name, file_name, request_mode)

    async def get(self, task_id: str) -> AnalysisTaskResponse | None:
        return await self._store.get(task_id)

    async def process(
        self,
        task_id: str,
        enterprise_name: str,
        file_name: str,
        local_path: Path,
        request_mode: str = "rule_demo",
    ) -> None:
        try:
            result = await self._service.run_product(
                TechnologyAnalysisInput(
                    task_id=task_id,
                    enterprise_name=enterprise_name,
                    pdf_path=local_path,
                    original_file_name=file_name,
                ), request_mode
            )
            await self._store.complete(task_id, result)
        except AnalysisServiceError as exc:
            logger.warning("Analysis task %s failed with %s", task_id, exc.code)
            await self._store.fail(
                task_id,
                AnalysisTaskError(code=exc.code, message=exc.message),
            )
        except Exception:
            logger.exception("Unexpected failure in analysis task %s", task_id)
            await self._store.fail(
                task_id,
                AnalysisTaskError(
                    code="analysis_failed",
                    message="分析流程执行失败，请稍后重试。",
                ),
            )


@lru_cache
def get_analysis_task_manager() -> AnalysisTaskManager:
    runtime_root = PROJECT_ROOT / "runtime" / "analysis"
    return AnalysisTaskManager(
        service=TechnologyAssessmentService(
            project_root=PROJECT_ROOT,
            runtime_root=runtime_root / "knowledge_bases",
        ),
        upload_root=runtime_root / "uploads",
    )
