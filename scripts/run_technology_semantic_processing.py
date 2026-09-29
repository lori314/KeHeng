"""Run adaptive technology-semantic processing for already-ingested company knowledge."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.knowledge.identity import company_for_name  # noqa: E402
from app.knowledge.semantic.processor import TechnologyKnowledgeProcessor  # noqa: E402
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase  # noqa: E402
from app.llm import OpenAICompatibleStructuredModel, StructuredModelError  # noqa: E402


async def run(enterprise_name: str) -> Path:
    settings = get_settings()
    if not (settings.llm_endpoint and settings.llm_model and settings.llm_api_key):
        raise StructuredModelError(
            "provider_not_configured",
            "Set KEHENG_LLM_ENDPOINT, KEHENG_LLM_MODEL and KEHENG_LLM_API_KEY",
        )
    company = company_for_name(enterprise_name)
    knowledge_base = SharedKnowledgeBase()
    try:
        stored_company = knowledge_base.repository.get_company(company.company_id or "")
        if stored_company is None:
            raise ValueError("No shared knowledge exists for this company name; run web research first")
        chunks = knowledge_base.repository.list_current_chunks(
            stored_company.company_id or "", "general"
        )
        if not chunks:
            raise ValueError("No current GENERAL knowledge exists for this company; run web research first")
        model = OpenAICompatibleStructuredModel(
            settings.llm_endpoint,
            settings.llm_model,
            settings.llm_api_key,
            timeout=settings.llm_timeout_seconds,
        )
        profile = await TechnologyKnowledgeProcessor(model, knowledge_base).process_company(
            stored_company.company_id or ""
        )
        output_dir = ROOT / "runtime" / "technology_semantic"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"semantic-{profile.profile_id}-{uuid.uuid4().hex[:8]}.json"
        output_path.write_text(
            json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path
    finally:
        knowledge_base.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enterprise_name")
    args = parser.parse_args()
    try:
        path = asyncio.run(run(args.enterprise_name))
    except StructuredModelError as exc:
        print(json.dumps({"error": exc.category, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(json.dumps({"error": "knowledge_not_found", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
