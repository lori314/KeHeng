"""Deterministic industry evaluation built on the existing weight contracts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Protocol

from evaluation.scoring import (
    FixedWeightProvider,
    IndicatorDefinition,
    IndicatorSet,
    WeightSet,
    load_indicator_set,
    load_weight_set,
)


class SupportsModelDump(Protocol):
    def model_dump(self, *, mode: str = "python") -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class IndustryEvaluationResult:
    industry_score: float | None
    dimension_scores: dict[str, float | None]
    score_explanation: list[dict[str, Any]]
    evidence_mapping: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "industry_score": self.industry_score,
            "dimension_scores": self.dimension_scores,
            "score_explanation": self.score_explanation,
            "evidence_mapping": self.evidence_mapping,
        }


class IndustryEvaluationEngine:
    """Calculate a weighted industry score from evidence-bound observations."""

    def __init__(self, indicator_set: IndicatorSet, weight_provider: FixedWeightProvider):
        self.indicator_set = indicator_set
        self.weight_set = weight_provider.get_weights(indicator_set)
        self._validate_default_weights()

    @classmethod
    def from_yaml(
        cls,
        indicators_path: str | Path,
        weights_path: str | Path,
    ) -> "IndustryEvaluationEngine":
        return cls(
            load_indicator_set(indicators_path),
            FixedWeightProvider(load_weight_set(weights_path)),
        )

    def evaluate(
        self, analysis: Mapping[str, Any] | SupportsModelDump
    ) -> IndustryEvaluationResult:
        payload = (
            analysis.model_dump(mode="python")
            if hasattr(analysis, "model_dump")
            else dict(analysis)
        )
        raw_indicators = payload.get("industry_indicators") or {}
        evidence_index = {
            str(item.get("evidence_id")): item
            for item in payload.get("evidence") or []
            if isinstance(item, Mapping) and item.get("evidence_id")
        }
        scored: dict[str, float] = {}
        explanations: list[dict[str, Any]] = []
        evidence_mapping: list[dict[str, Any]] = []
        for definition in self.indicator_set.indicators:
            raw = raw_indicators.get(definition.indicator_id)
            score, evidence_ids, missing_reason = self._validated_observation(
                definition, raw, evidence_index
            )
            weight = self.weight_set.weights[definition.indicator_id]
            if score is None:
                explanations.append(
                    {
                        "indicator_id": definition.indicator_id,
                        "indicator_name": definition.name,
                        "status": "unscored",
                        "reason": missing_reason,
                        "weight": weight,
                        "weighted_score": None,
                    }
                )
            else:
                scored[definition.indicator_id] = score
                weighted_score = score * weight
                explanations.append(
                    {
                        "indicator_id": definition.indicator_id,
                        "indicator_name": definition.name,
                        "status": "scored",
                        "input_score": score,
                        "weight": weight,
                        "weighted_score": round(weighted_score, 4),
                        "calculation": f"{score:g} × {weight:.2%} = {weighted_score:.2f}",
                        "indicator_version": self.indicator_set.version,
                        "indicator_set_id": self.indicator_set.set_id,
                        "weight_version": self.weight_set.version,
                        "weight_set_id": self.weight_set.set_id,
                    }
                )
            evidence_mapping.append(
                {
                    "indicator_id": definition.indicator_id,
                    "evidence_ids": evidence_ids,
                    "sources": [evidence_index[item] for item in evidence_ids],
                }
            )

        available_weight = sum(
            self.weight_set.weights[indicator_id] for indicator_id in scored
        )
        industry_score = (
            round(
                sum(
                    score * self.weight_set.weights[indicator_id]
                    for indicator_id, score in scored.items()
                )
                / available_weight,
                2,
            )
            if available_weight > 0
            else None
        )
        dimension_scores = {
            dimension: self._dimension_score(indicators, scored)
            for dimension, indicators in self.indicator_set.dimensions.items()
        }
        if available_weight < 1.0:
            explanations.append(
                {
                    "status": "partial_score" if available_weight else "unscored",
                    "reason": (
                        "缺失产业指标不按零分处理；产业分按已有指标权重重新归一化。"
                        if available_weight
                        else "没有具备有效证据的可评分产业指标，产业分未计算。"
                    ),
                    "available_weight": round(available_weight, 4),
                }
            )
        return IndustryEvaluationResult(
            industry_score=industry_score,
            dimension_scores=dimension_scores,
            score_explanation=explanations,
            evidence_mapping=evidence_mapping,
        )

    @staticmethod
    def _validated_observation(
        definition: IndicatorDefinition,
        raw: Any,
        evidence_index: Mapping[str, Mapping[str, Any]],
    ) -> tuple[float | None, list[str], str | None]:
        if not isinstance(raw, Mapping) or raw.get("score") is None:
            return None, [], "指标缺失或未评分"
        if isinstance(raw["score"], bool):
            raise ValueError(f"{definition.indicator_id} score must be numeric")
        score = float(raw["score"])
        if not math.isfinite(score):
            raise ValueError(f"{definition.indicator_id} score must be finite")
        if score < definition.minimum or score > definition.maximum:
            raise ValueError(
                f"{definition.indicator_id} score {score} is outside "
                f"[{definition.minimum}, {definition.maximum}]"
            )
        evidence_ids = list(dict.fromkeys(str(item) for item in raw.get("evidence", [])))
        valid_ids = [item for item in evidence_ids if item in evidence_index]
        if definition.evidence_required and not valid_ids:
            return None, valid_ids, "缺少可定位的有效证据，指标未评分"
        return score, valid_ids, None

    def _dimension_score(
        self, indicator_ids: tuple[str, ...], scored: Mapping[str, float]
    ) -> float | None:
        available_ids = [item for item in indicator_ids if item in scored]
        available_weight = sum(self.weight_set.weights[item] for item in available_ids)
        if available_weight == 0:
            return None
        return round(
            sum(scored[item] * self.weight_set.weights[item] for item in available_ids)
            / available_weight,
            2,
        )

    def _validate_default_weights(self) -> None:
        for definition in self.indicator_set.indicators:
            configured = self.weight_set.weights[definition.indicator_id]
            if abs(configured - definition.default_weight) > 1e-9:
                raise ValueError(
                    f"Fixed weight mismatch for {definition.indicator_id}: "
                    f"indicator={definition.default_weight}, weights={configured}"
                )
