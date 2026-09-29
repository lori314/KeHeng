"""Technology report generation endpoint."""

from fastapi import APIRouter, HTTPException

from app.report import ReportGenerator, ReportRequest, TechnologyReport

router = APIRouter(prefix="/report", tags=["report"])
generator = ReportGenerator()


@router.post(
    "/generate",
    response_model=TechnologyReport,
    summary="生成可解释技术价值报告",
)
async def generate_report(request: ReportRequest) -> TechnologyReport:
    try:
        return generator.generate(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
