"""Run an explicitly configured real LLM + Tavily research smoke test."""

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
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase  # noqa: E402
from app.research.orchestrator import IterativeResearchService  # noqa: E402
from app.research.planner import (  # noqa: E402
    OpenAICompatibleStructuredModel,
    RetrievalPlanner,
    StructuredModelError,
)
from app.research.search_provider import SearchProviderError  # noqa: E402
from app.research.tavily_provider import TavilySearchProvider  # noqa: E402


async def run(args: argparse.Namespace) -> Path:
    settings = get_settings()
    if settings.web_search_provider != "tavily" or not settings.tavily_api_key:
        raise SearchProviderError(
            "provider_not_configured",
            "Set KEHENG_WEB_SEARCH_PROVIDER=tavily and KEHENG_TAVILY_API_KEY",
        )
    if not (settings.llm_endpoint and settings.llm_model and settings.llm_api_key):
        raise StructuredModelError(
            "provider_not_configured",
            "Set KEHENG_LLM_ENDPOINT, KEHENG_LLM_MODEL and KEHENG_LLM_API_KEY",
        )

    model = OpenAICompatibleStructuredModel(
        settings.llm_endpoint,
        settings.llm_model,
        settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    search = TavilySearchProvider(
        settings.tavily_api_key,
        search_depth=settings.tavily_search_depth,
        timeout=settings.web_search_timeout_seconds,
    )
    knowledge_base = SharedKnowledgeBase()
    try:
        service = IterativeResearchService(
            RetrievalPlanner(model),
            search,
            knowledge_base,
        )
        result = await service.run(
            args.enterprise_name,
            max_rounds=args.max_rounds,
            max_queries_per_round=args.max_queries_per_round,
            max_results_per_query=args.max_results_per_query,
        )
        runtime_dir = ROOT / "runtime" / "research"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        trace_path = runtime_dir / f"research-{uuid.uuid4().hex}.json"
        trace_path.write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return trace_path
    finally:
        knowledge_base.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enterprise_name")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--max-queries-per-round", type=int, default=6)
    parser.add_argument("--max-results-per-query", type=int, default=5)
    args = parser.parse_args()
    try:
        path = asyncio.run(run(args))
    except (SearchProviderError, StructuredModelError) as exc:
        print(
            json.dumps(
                {"error": exc.category, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
