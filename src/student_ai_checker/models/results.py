"""Strict models for AI-proposed criterion results."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CriterionStatus(str, Enum):
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    NOT_COMPLETED = "not_completed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNABLE_TO_CHECK = "unable_to_check"

    @property
    def label_ru(self) -> str:
        return {
            CriterionStatus.COMPLETED: "Выполнено",
            CriterionStatus.PARTIALLY_COMPLETED: "Выполнено частично",
            CriterionStatus.NOT_COMPLETED: "Не выполнено",
            CriterionStatus.INSUFFICIENT_EVIDENCE: "Недостаточно подтверждений",
            CriterionStatus.UNABLE_TO_CHECK: "Не удалось проверить",
        }[self]


class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    max_score: float = Field(gt=0)
    proposed_score: float | None = Field(default=None, ge=0)
    status: CriterionStatus
    explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    student_feedback: str = ""
    manual_review_required: bool = False
    manual_review_reason: str | None = None

    @model_validator(mode="after")
    def validate_manual_review_fields(self) -> "CriterionResult":
        unresolved = {
            CriterionStatus.INSUFFICIENT_EVIDENCE,
            CriterionStatus.UNABLE_TO_CHECK,
        }
        if self.status in unresolved:
            if self.proposed_score is not None:
                raise ValueError("unresolved status must not contain proposed_score")
            if not self.manual_review_required:
                raise ValueError("unresolved status requires manual review")
        elif self.proposed_score is None:
            raise ValueError("resolved status requires proposed_score")

        if self.manual_review_required and not self.manual_review_reason:
            raise ValueError("manual_review_reason is required when manual review is required")
        return self


class GradingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[CriterionResult] = Field(min_length=1)
