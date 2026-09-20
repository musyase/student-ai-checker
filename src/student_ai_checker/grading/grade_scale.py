"""Configured conversion of criterion points into a Russian 2–5 grade."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GradeScale(BaseModel):
    """Minimum percentage required for each grade.

    The scale is part of the teacher-approved assignment snapshot. Keeping it
    there makes the conversion explicit and prevents the model from inventing
    grading boundaries.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    grade_2_min_percent: float = Field(ge=0, le=100)
    grade_3_min_percent: float = Field(ge=0, le=100)
    grade_4_min_percent: float = Field(ge=0, le=100)
    grade_5_min_percent: float = Field(ge=0, le=100)

    @classmethod
    def standard(cls) -> "GradeScale":
        """The project-wide default scale for all assignments."""
        return cls(
            grade_2_min_percent=0,
            grade_3_min_percent=30,
            grade_4_min_percent=50,
            grade_5_min_percent=70,
        )

    @model_validator(mode="after")
    def validate_thresholds(self) -> "GradeScale":
        thresholds = (
            self.grade_2_min_percent,
            self.grade_3_min_percent,
            self.grade_4_min_percent,
            self.grade_5_min_percent,
        )
        if thresholds[0] != 0:
            raise ValueError("grade_2_min_percent must be 0")
        if any(left >= right for left, right in zip(thresholds, thresholds[1:])):
            raise ValueError("grade thresholds must be strictly increasing")
        return self

    def grade_for(self, points: float, maximum: float) -> int:
        """Return the grade for a complete score, without rounding points."""
        if maximum <= 0 or points < 0 or points > maximum:
            raise ValueError("points must be within a positive maximum score")
        percent = Decimal(str(points)) * Decimal("100") / Decimal(str(maximum))
        thresholds = (
            (5, self.grade_5_min_percent),
            (4, self.grade_4_min_percent),
            (3, self.grade_3_min_percent),
            (2, self.grade_2_min_percent),
        )
        for grade, threshold in thresholds:
            if percent >= Decimal(str(threshold)):
                return grade
        return 2
