"""Shared generic bilingual retrieval queries used by products and evals."""

import json
from pathlib import Path

_DEFINITIONS = Path(__file__).resolve().parents[3] / "evaluation" / "retrieval_indicator_definitions.json"


def indicator_queries(indicator: str) -> list[str]:
    data = json.loads(_DEFINITIONS.read_text(encoding="utf-8"))
    queries = data["definitions"][indicator]
    if len(queries) != int(data["query_count"]):
        raise ValueError(f"query definition count mismatch for {indicator}")
    return list(queries)


def domain_queries(domain: str) -> tuple[str, ...]:
    indicators = {
        "technology": ("technical_autonomy", "innovation_capability", "intellectual_property", "technical_maturity"),
        "industry": ("market_potential", "industry_growth", "competitive_position", "policy_environment"),
    }[domain]
    # Four indicator-specific generic queries, each combining paired Chinese/English definitions.
    return tuple(" ".join(indicator_queries(indicator)) for indicator in indicators)
