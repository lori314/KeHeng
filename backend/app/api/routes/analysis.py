"""Asynchronous-style upload and polling endpoints for technology analysis."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from app.core.config import get_settings
from app.services.analysis_tasks import (
    AnalysisCreateResponse,
    AnalysisTaskManager,
    AnalysisTaskResponse,
    get_analysis_task_manager,
)


router = APIRouter(prefix="/analysis", tags=["analysis"])
_allowed_content_types = {"application/pdf", "application/octet-stream", ""}
_write_chunk_size = 1024 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_ITERATION04_RESULT = _PROJECT_ROOT / "runtime" / "review" / "paired_model" / "run-20260928T110017Z-2bc83078" / "paired_model_results.json"
_PUBLIC_DEMO_PDF = _PROJECT_ROOT / "data" / "examples" / "public_test_company_technology_profile.pdf"


@router.get("/demo-material", summary="下载项目合成演示材料")
async def download_demo_material():
    from fastapi.responses import FileResponse

    if not _PUBLIC_DEMO_PDF.is_file():
        raise HTTPException(status_code=404, detail={"code": "demo_material_unavailable", "message": "合成演示 PDF 不存在。"})
    return FileResponse(_PUBLIC_DEMO_PDF, media_type="application/pdf", filename="synthetic_technology_profile.pdf")


@router.get("/replays/iteration04-nio-hash/availability", summary="检查本机是否有 NIO 历史报告")
async def replay_iteration04_nio_hash_availability() -> dict[str, bool]:
    """Keep the optional replay control out of clean installs without local data."""

    return {"available": _ITERATION04_RESULT.is_file()}


@router.get("/replays/iteration04-nio-hash", summary="读取已保存的 NIO 模型报告，不触发模型调用")
async def replay_iteration04_nio_hash() -> dict:
    if not _ITERATION04_RESULT.is_file():
        raise HTTPException(status_code=404, detail={"code": "saved_replay_unavailable", "message": "本机未包含第四轮保存结果；此入口只读取本地已保存记录，不会调用模型。"})
    try:
        payload = json.loads(_ITERATION04_RESULT.read_text(encoding="utf-8"))
        result = next(item for item in payload["results"] if item.get("company_id") == "nio" and item.get("retriever") == "hash")
        if not isinstance(result.get("report"), dict):
            raise ValueError("saved report missing")
    except (OSError, ValueError, KeyError, StopIteration, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail={"code": "saved_replay_invalid", "message": "保存的报告记录无法读取。"}) from exc
    run_info = dict(result.get("run_info") or {})
    run_info["saved_result_replay"] = {
        "kind": "saved_real_model_result",
        "model": payload.get("model", {}).get("name"),
        "run_date": result.get("started_at_utc"),
        "retriever": result.get("retriever"),
        "live_model_call": False,
    }
    modules = result.get("modules") or {}
    failures = {
        name: item["failure"]
        for name, item in modules.items()
        if isinstance(item, dict) and item.get("failure")
    }
    return {
        "task_id": f"saved-{result['company_id']}-{result['retriever']}",
        "status": "partial" if result.get("status") == "partial" else "completed",
        "enterprise_name": result["report"].get("enterprise_name", "NIO"),
        "file_name": "local archived result",
        "report": result["report"],
        "modules": modules,
        "module_failures": failures,
        "result_status": "partial" if result.get("status") == "partial" else "completed",
        "run_info": run_info,
        "request_mode": "real_model",
        "error": None,
    }


@router.post(
    "/create",
    response_model=AnalysisCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="创建企业技术资料分析任务",
)
async def create_analysis(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="文本型 PDF 技术资料")],
    enterprise_name: Annotated[str, Form(min_length=1, max_length=120)],
    manager: Annotated[AnalysisTaskManager, Depends(get_analysis_task_manager)],
    analysis_mode: Annotated[str, Form()] = "rule_demo",
) -> AnalysisCreateResponse:
    normalized_name = enterprise_name.strip()
    if not normalized_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "blank_enterprise_name", "message": "企业名称不能为空。"},
        )
    if analysis_mode not in {"rule_demo", "real_model"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_analysis_mode", "message": "请选择规则演示或真实模型分析。"})

    original_name = Path(file.filename or "").name
    content_type = (file.content_type or "").lower()
    if Path(original_name).suffix.lower() != ".pdf" or content_type not in _allowed_content_types:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "unsupported_file_type", "message": "仅支持 PDF 文件。"},
        )

    task_id = manager.new_task_id()
    target_path = manager.upload_path(task_id)
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    try:
        size, header = await _persist_upload(file, target_path, max_bytes)
    finally:
        await file.close()

    if size == 0:
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "empty_file", "message": "上传文件为空。"},
        )
    if not header.startswith(b"%PDF-"):
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "invalid_pdf_signature", "message": "文件内容不是有效的 PDF。"},
        )

    record = await manager.create(task_id, normalized_name, original_name, analysis_mode)
    background_tasks.add_task(
        manager.process,
        task_id,
        normalized_name,
        original_name,
        target_path,
        analysis_mode,
    )
    return AnalysisCreateResponse(task_id=record.task_id, status=record.status)


@router.get(
    "/{task_id}",
    response_model=AnalysisTaskResponse,
    summary="查询企业技术资料分析任务",
)
async def get_analysis(
    task_id: str,
    manager: Annotated[AnalysisTaskManager, Depends(get_analysis_task_manager)],
) -> AnalysisTaskResponse:
    record = await manager.get(task_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "task_not_found", "message": "分析任务不存在。"},
        )
    return record


async def _persist_upload(
    upload: UploadFile, target_path: Path, max_bytes: int
) -> tuple[int, bytes]:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    header = b""
    try:
        with target_path.open("wb") as target:
            while chunk := await upload.read(_write_chunk_size):
                if not header:
                    header = chunk[:8]
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail={
                            "code": "file_too_large",
                            "message": f"PDF 大小不能超过 {max_bytes // (1024 * 1024)} MB。",
                        },
                    )
                target.write(chunk)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    return total, header
