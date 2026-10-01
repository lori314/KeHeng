"""Top-level API router."""

from fastapi import APIRouter

from app.api.routes.analysis import router as analysis_router
from app.api.routes.health import router as health_router
from app.api.routes.report import router as report_router
from app.api.routes.report_v2 import router as report_v2_router
from app.api.routes.evidence_analysis import router as evidence_analysis_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(analysis_router)
api_router.include_router(report_router)
api_router.include_router(report_v2_router)
api_router.include_router(evidence_analysis_router)
