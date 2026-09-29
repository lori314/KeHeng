"""Run evidence-based joint technology-finance reasoning for an ingested company."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.finance.processor import TechnologyFinanceProcessor  # noqa: E402
from app.knowledge.identity import company_for_name  # noqa: E402
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase  # noqa: E402
from app.llm import OpenAICompatibleStructuredModel, StructuredModelError  # noqa: E402


async def run(company_name: str) -> dict:
    settings = get_settings()
    if not (settings.llm_endpoint and settings.llm_model and settings.llm_api_key):
        raise StructuredModelError("provider_not_configured", "Set KEHENG_LLM_ENDPOINT, KEHENG_LLM_MODEL and KEHENG_LLM_API_KEY")
    company_id = company_for_name(company_name).company_id or ""
    kb = SharedKnowledgeBase()
    try:
        if kb.repository.get_technology_semantic_profile(company_id) is None:
            raise ValueError("No TechnologySemanticProfile exists; run scripts/run_technology_semantic_processing.py first")
        model = OpenAICompatibleStructuredModel(settings.llm_endpoint, settings.llm_model, settings.llm_api_key, timeout=settings.llm_timeout_seconds)
        profile = await TechnologyFinanceProcessor(model, kb).process_company(company_id)
        return profile.model_dump(mode="json")
    finally:
        kb.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company_name")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args.company_name)), ensure_ascii=False, indent=2))
    except (StructuredModelError, ValueError) as exc:
        category = exc.category if isinstance(exc, StructuredModelError) else "knowledge_not_found"
        print(json.dumps({"error": category, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
