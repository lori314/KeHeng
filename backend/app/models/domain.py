"""Shared domain enumerations for future task persistence."""

from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class TaskStage(StrEnum):
    UPLOAD = "upload"
    PARSING = "parsing"
    INDEXING = "indexing"
    ANALYSIS = "analysis"
    EVALUATION = "evaluation"
    REPORT = "report"
