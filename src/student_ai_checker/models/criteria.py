"""Trusted grading criteria models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import EvidenceKind


class EvidenceRequirement(BaseModel):
    """Evidence expected for a criterion."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    kinds: list[EvidenceKind] = Field(default_factory=list)
    required: bool = True


class Criterion(BaseModel):
    """A teacher-approved criterion. AI is not allowed to mutate this model."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    max_score: float = Field(gt=0)
    allowed_scores: list[float] = Field(min_length=1)
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)

    @field_validator("allowed_scores")
    @classmethod
    def validate_unique_scores(cls, values: list[float]) -> list[float]:
        if len(set(values)) != len(values):
            raise ValueError("allowed_scores must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_score_range(self) -> "Criterion":
        if any(score < 0 or score > self.max_score for score in self.allowed_scores):
            raise ValueError("allowed_scores must be between 0 and max_score")
        if self.max_score not in self.allowed_scores:
            raise ValueError("allowed_scores must include max_score")
        return self


class CriteriaSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    criteria: list[Criterion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "CriteriaSet":
        ids = [criterion.id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion ids must be unique")
        return self

    def by_id(self) -> dict[str, Criterion]:
        return {criterion.id: criterion for criterion in self.criteria}
