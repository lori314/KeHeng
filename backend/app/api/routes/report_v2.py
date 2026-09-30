"""Read-only API for assembling V2 evidence-first company reports."""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.report_v2 import EvidenceFirstReport, EvidenceFirstReportAssembler

router = APIRouter(prefix="/api/report/v2", tags=["report-v2"])
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


@router.get("/company/{company_id}", response_model=EvidenceFirstReport)
async def get_evidence_first_company_report(company_id: str) -> EvidenceFirstReport:
    knowledge_base = SharedKnowledgeBase(_PROJECT_ROOT / "runtime" / "knowledge")
    try:
        return EvidenceFirstReportAssembler(knowledge_base.repository).build(company_id)
    except ValueError as exc:
        raw_code = str(exc)
        known_codes = {
            "missing_company", "missing_technology_profile", "missing_finance_profile",
            "missing_assertion_profile", "stale_profile_chain", "missing_source",
            "missing_technology_fact", "missing_financial_fact", "missing_assertion_fact",
            "missing_milestone_reference", "unknown_template", "unknown_milestone",
            "unknown_finance_rule",
        }
        code = raw_code if raw_code in known_codes else "report_assembly_failed"
        status = 404 if code in {"missing_company", "missing_technology_profile", "missing_finance_profile", "missing_assertion_profile"} else 409
        messages = {
            "missing_company": "Company record was not found.",
            "missing_technology_profile": "Technology semantic profile is unavailable.",
            "missing_finance_profile": "Matching technology-finance profile is unavailable.",
            "missing_assertion_profile": "Matching evidence assertion profile is unavailable.",
            "stale_profile_chain": "Persisted profiles do not belong to one consistent processing run.",
            "missing_source": "A cited source record is unavailable.",
        }
        raise HTTPException(status_code=status, detail={"code": code, "message": messages.get(code, "Report provenance could not be resolved.")}) from exc
