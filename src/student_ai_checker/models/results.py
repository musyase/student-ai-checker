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
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "description": (
                "Result for one approved criterion. Cross-field rules are mandatory: "
                "for insufficient_evidence or unable_to_check, proposed_score must be null, "
                "manual_review_required must be true, and manual_review_reason must be a "
                "non-empty string. For completed, partially_completed, or not_completed, "
                "proposed_score must be a number."
            )
        },
    )

    criterion_id: str = Field(
        min_length=1,
        description="Exact ID of the approved criterion; copy it without changes.",
    )
    title: str = Field(
        min_length=1,
        description="Exact title of the approved criterion; copy it without changes.",
    )
    max_score: float = Field(
        gt=0,
        description="Exact max_score of the approved criterion; do not change it.",
    )
    proposed_score: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Proposed criterion score. MUST be null when status is insufficient_evidence "
            "or unable_to_check. MUST be numeric for completed, partially_completed, "
            "or not_completed."
        ),
    )
    status: CriterionStatus = Field(
        description=(
            "Criterion status. If insufficient_evidence or unable_to_check is selected, "
            "manual_review_required MUST be true, proposed_score MUST be null, and "
            "manual_review_reason MUST be non-empty."
        )
    )
    explanation: str = Field(
        min_length=1,
        description="Short explanation grounded only in cited evidence_ids.",
    )
    evidence_ids: list[str] = Field(
        min_length=1,
        description="Only evidence IDs that exist in the supplied student document payload.",
    )
    student_feedback: str = Field(
        default="",
        description="Concise feedback for the student; do not invent unsupported facts.",
    )
    manual_review_required: bool = Field(
        default=False,
        description=(
            "MUST be true for insufficient_evidence or unable_to_check. May also be true "
            "for another status when a teacher should verify a material uncertainty."
        ),
    )
    manual_review_reason: str | None = Field(
        default=None,
        description=(
            "MUST be a non-empty reason whenever manual_review_required is true; otherwise "
            "it may be null."
        ),
    )

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
