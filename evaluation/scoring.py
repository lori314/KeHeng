"""Deterministic, configuration-driven technology evaluation engine.

The Agent supplies evidence-bound indicator observations. This module owns
weight validation, missing-value handling, aggregation and score explanation.
It intentionally has no dependency on an LLM or a model provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Protocol

import yaml


class SupportsModelDump(Protocol):
    def model_dump(self, *, mode: str = "python") -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class IndicatorDefinition:
    indicator_id: str
    name: str
    description: str
    dimension: str
    minimum: float
    maximum: float
    default_weight: float
    evidence_required: bool


@dataclass(frozen=True, slots=True)
class IndicatorSet:
    set_id: str
    version: str
    indicators: tuple[IndicatorDefinition, ...]
    dimensions: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class WeightSet:
    set_id: str
    version: str
    method: str
    indicator_set_id: str
    weights: dict[str, float]


class WeightProvider(ABC):
    """Replaceable weight source; an AHP provider can implement this later."""

    @abstractmethod
    def get_weights(self, indicator_set: IndicatorSet) -> WeightSet:
        if self._weight_set.indicator_set_id != indicator_set.set_id:
            raise ValueError(
                "Weight set targets "
                f"{self._weight_set.indicator_set_id}, not {indicator_set.set_id}"
            )
        if self._weight_set.method != "fixed":
            raise ValueError("FixedWeightProvider requires a fixed weight set")
        raise NotImplementedError


class FixedWeightProvider(WeightProvider):
    def __init__(self, weight_set: WeightSet) -> None:
        self._weight_set = weight_set

    def get_weights(self, indicator_set: IndicatorSet) -> WeightSet:
        configured_ids = set(self._weight_set.weights)
        expected_ids = {item.indicator_id for item in indicator_set.indicators}
        if configured_ids != expected_ids:
            missing = sorted(expected_ids - configured_ids)
            unknown = sorted(configured_ids - expected_ids)
            raise ValueError(
                f"Weight indicators do not match indicator set; missing={missing}, "
                f"unknown={unknown}"
            )
        total = sum(self._weight_set.weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"Weights must sum to 1.0, got {total:.12f}")
        if any(weight < 0 for weight in self._weight_set.weights.values()):
            raise ValueError("Weights must be non-negative")
        return self._weight_set


class AhpWeightProvider(WeightProvider):
    """Reserved integration point for reviewed AHP weight calculation."""

    def get_weights(self, indicator_set: IndicatorSet) -> WeightSet:
        raise NotImplementedError(
            "AHP weighting is reserved for a future reviewed implementation"
        )


@dataclass(frozen=True, slots=True)
class TechnologyEvaluationResult:
    technology_score: float | None
    dimension_scores: dict[str, float | None]
    score_explanation: list[dict[str, Any]]
    evidence_mapping: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "technology_score": self.technology_score,
            "dimension_scores": self.dimension_scores,
            "score_explanation": self.score_explanation,
            "evidence_mapping": self.evidence_mapping,
        }


class TechnologyEvaluationEngine:
    """Calculate a transparent weighted score from Technology Agent output."""

    def __init__(
        self,
        indicator_set: IndicatorSet,
        weight_provider: WeightProvider,
    ) -> None:
        self.indicator_set = indicator_set
        self.weight_set = weight_provider.get_weights(indicator_set)
        self._validate_default_weights()

    @classmethod
    def from_yaml(
        cls,
        indicators_path: str | Path,
        weights_path: str | Path,
    ) -> TechnologyEvaluationEngine:
        indicator_set = load_indicator_set(indicators_path)
        weight_set = load_weight_set(weights_path)
        return cls(indicator_set, FixedWeightProvider(weight_set))

    def evaluate(
        self, analysis: Mapping[str, Any] | SupportsModelDump
    ) -> TechnologyEvaluationResult:
        payload = (
            analysis.model_dump(mode="python")
            if hasattr(analysis, "model_dump")
            else dict(analysis)
        )
        raw_indicators = payload.get("technology_indicators") or {}
        if not isinstance(raw_indicators, Mapping):
            raw_indicators = {}
        raw_evidence = payload.get("evidence") or []
        evidence_index = {
            str(item.get("evidence_id")): item
            for item in raw_evidence
            if isinstance(item, Mapping) and item.get("evidence_id")
        }

        scored: dict[str, float] = {}
        explanation: list[dict[str, Any]] = []
        evidence_mapping: list[dict[str, Any]] = []

        for definition in self.indicator_set.indicators:
            raw = raw_indicators.get(definition.indicator_id)
            score, evidence_ids, missing_reason = self._validated_observation(
                definition, raw, evidence_index
            )
            weight = self.weight_set.weights[definition.indicator_id]
            if score is None:
                explanation.append(
                    {
                        "indicator_id": definition.indicator_id,
                        "indicator_name": definition.name,
                        "status": "unscored",
                        "reason": missing_reason,
                        "weight": weight,
                        "weighted_score": None,
                    }
                )
                evidence_mapping.append(
                    {
                        "indicator_id": definition.indicator_id,
                        "evidence_ids": evidence_ids,
                        "sources": [evidence_index[item] for item in evidence_ids],
                    }
                )
                continue

            scored[definition.indicator_id] = score
            weighted_score = score * weight
            explanation.append(
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
        technology_score = (
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
            dimension: self._dimension_score(indicator_ids, scored)
            for dimension, indicator_ids in self.indicator_set.dimensions.items()
        }
        if available_weight < 1.0:
            explanation.append(
                {
                    "status": "partial_score" if available_weight else "unscored",
                    "reason": (
                        "缺失指标不按零分处理；总分按已有指标权重重新归一化。"
                        if available_weight
                        else "没有具备有效证据的可评分指标，技术总分未计算。"
                    ),
                    "available_weight": round(available_weight, 4),
                }
            )

        return TechnologyEvaluationResult(
            technology_score=technology_score,
            dimension_scores=dimension_scores,
            score_explanation=explanation,
            evidence_mapping=evidence_mapping,
        )

    def _validated_observation(
        self,
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
        value = sum(
            scored[item] * self.weight_set.weights[item] for item in available_ids
        ) / available_weight
        return round(value, 2)

    def _validate_default_weights(self) -> None:
        for definition in self.indicator_set.indicators:
            configured = self.weight_set.weights[definition.indicator_id]
            if abs(configured - definition.default_weight) > 1e-9:
                raise ValueError(
                    f"Fixed weight mismatch for {definition.indicator_id}: "
                    f"indicator={definition.default_weight}, weights={configured}"
                )


def load_indicator_set(path: str | Path) -> IndicatorSet:
    payload = _load_yaml(path)
    raw_set = payload["indicator_set"]
    indicators = tuple(
        IndicatorDefinition(
            indicator_id=str(item["id"]),
            name=str(item["name"]),
            description=str(item["description"]),
            dimension=str(item["dimension"]),
            minimum=float(item["score_range"]["min"]),
            maximum=float(item["score_range"]["max"]),
            default_weight=float(item["weight"]),
            evidence_required=bool(item.get("evidence_required", True)),
        )
        for item in raw_set["indicators"]
    )
    dimensions = {
        str(dimension_id): tuple(str(item) for item in config["indicators"])
        for dimension_id, config in payload["dimensions"].items()
    }
    configured_ids = {item.indicator_id for item in indicators}
    if len(configured_ids) != len(indicators):
        raise ValueError("Indicator IDs must be unique")
    dimension_ids = {item for values in dimensions.values() for item in values}
    dimension_entry_count = sum(len(values) for values in dimensions.values())
    if configured_ids != dimension_ids or dimension_entry_count != len(configured_ids):
        raise ValueError("Each indicator must belong to exactly one configured dimension")
    return IndicatorSet(
        set_id=str(raw_set["id"]),
        version=str(payload["schema_version"]),
        indicators=indicators,
        dimensions=dimensions,
    )


def load_weight_set(path: str | Path) -> WeightSet:
    payload = _load_yaml(path)
    raw_set = payload["weight_set"]
    return WeightSet(
        set_id=str(raw_set["id"]),
        version=str(payload["schema_version"]),
        method=str(raw_set["method"]),
        indicator_set_id=str(raw_set["indicator_set_id"]),
        weights={str(key): float(value) for key, value in raw_set["weights"].items()},
    )


def _load_yaml(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Evaluation configuration not found: {resolved}")
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Evaluation configuration must be a mapping: {resolved}")
    return payload
