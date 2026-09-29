"""Configuration-driven combination of technology and industry scores."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from evaluation.industry_scoring import IndustryEvaluationResult
from evaluation.scoring import TechnologyEvaluationResult


@dataclass(frozen=True, slots=True)
class ComprehensiveEvaluationResult:
    technology_score: float | None
    industry_score: float | None
    overall_score: float | None
    dimension_scores: dict[str, float | None]
    score_explanation: list[dict[str, Any]]
    evidence_mapping: list[dict[str, Any]]
    assessment_status: str
    evidence_coverage: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "technology_score": self.technology_score,
            "industry_score": self.industry_score,
            "overall_score": self.overall_score,
            "dimension_scores": self.dimension_scores,
            "score_explanation": self.score_explanation,
            "evidence_mapping": self.evidence_mapping,
            "assessment_status": self.assessment_status,
            "evidence_coverage": self.evidence_coverage,
        }


class ComprehensiveEvaluationEngine:
    """Combine already-calculated dimension scores; never recalculates inputs."""

    def __init__(self, weights: Mapping[str, float], config_version: str):
        expected = {"technology", "industry"}
        if set(weights) != expected:
            raise ValueError("Comprehensive weights must contain technology and industry")
        if any(value < 0 for value in weights.values()):
            raise ValueError("Comprehensive weights must be non-negative")
        if abs(sum(weights.values()) - 1.0) > 1e-9:
            raise ValueError("Comprehensive weights must sum to 1.0")
        self.weights = dict(weights)
        self.config_version = config_version

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ComprehensiveEvaluationEngine":
        resolved = Path(path).expanduser().resolve()
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        raw = payload["weight_set"]
        return cls(
            {str(key): float(value) for key, value in raw["weights"].items()},
            str(payload["schema_version"]),
        )

    def evaluate(
        self,
        technology: TechnologyEvaluationResult | Mapping[str, Any],
        industry: IndustryEvaluationResult | Mapping[str, Any],
    ) -> ComprehensiveEvaluationResult:
        technology_payload = _as_mapping(technology)
        industry_payload = _as_mapping(industry)
        technology_score = _number_or_none(technology_payload.get("technology_score"))
        industry_score = _number_or_none(industry_payload.get("industry_score"))
        dimensions = {
            "technology": technology_score,
            "industry": industry_score,
        }
        available = {
            key: value for key, value in dimensions.items() if value is not None
        }
        technology_coverage = _coverage(technology_payload, 4)
        industry_coverage = _coverage(industry_payload, 4)
        evidence_coverage = {
            "technology": technology_coverage,
            "industry": industry_coverage,
            "overall": round((technology_coverage + industry_coverage) / 2, 4),
        }
        if technology_score is None or industry_score is None:
            assessment_status = "insufficient_evidence"
            overall = None
        elif technology_coverage >= 1.0 and industry_coverage >= 1.0:
            assessment_status = "fully_scored"
            overall = round(
                technology_score * self.weights["technology"]
                + industry_score * self.weights["industry"],
                2,
            )
        else:
            assessment_status = "partially_scored"
            overall = round(
                technology_score * self.weights["technology"]
                + industry_score * self.weights["industry"],
                2,
            )
        explanation = [
            {
                "dimension": key,
                "score": dimensions[key],
                "weight": self.weights[key],
                "status": "scored" if dimensions[key] is not None else "unscored",
                "config_version": self.config_version,
            }
            for key in ("technology", "industry")
        ]
        if assessment_status != "fully_scored":
            explanation.append(
                {
                    "status": assessment_status,
                    "reason": "证据不足时不对一级维度重新归一化；overall_score 只有在技术和产业维度均可评分时计算。",
                    "evidence_coverage": evidence_coverage,
                }
            )
        return ComprehensiveEvaluationResult(
            technology_score=technology_score,
            industry_score=industry_score,
            overall_score=overall,
            dimension_scores=dimensions,
            score_explanation=explanation,
            evidence_mapping=[
                *list(technology_payload.get("evidence_mapping") or []),
                *list(industry_payload.get("evidence_mapping") or []),
            ],
            assessment_status=assessment_status,
            evidence_coverage=evidence_coverage,
        )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if isinstance(value, Mapping):
        return value
    raise TypeError("Evaluation result must be a mapping or result object")


def _number_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def _coverage(payload: Mapping[str, Any], total: int) -> float:
    indicators = payload.get("evidence_mapping") or []
    scored = sum(1 for value in indicators if isinstance(value, Mapping) and value.get("evidence_ids"))
    return round(scored / total, 4) if total else 0.0
