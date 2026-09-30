"""Adaptive technology knowledge processing contracts and pipeline."""

from app.knowledge.semantic.contracts import (
    InterpreterReport,
    MilestoneObservation,
    TechnologyDomainProfile,
    TechnologyFact,
    TechnologySemanticProfile,
    TechnologyTemplateSelection,
)
from app.knowledge.semantic.processor import TechnologyKnowledgeProcessor
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry

__all__ = [
    "KnowledgeSemanticRegistry",
    "InterpreterReport",
    "MilestoneObservation",
    "TechnologyDomainProfile",
    "TechnologyFact",
    "TechnologyKnowledgeProcessor",
    "TechnologySemanticProfile",
    "TechnologyTemplateSelection",
]
