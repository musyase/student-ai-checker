from __future__ import annotations

import pytest

from student_ai_checker.grading.grade_scale import GradeScale


def scale() -> GradeScale:
    return GradeScale(
        grade_2_min_percent=0,
        grade_3_min_percent=30,
        grade_4_min_percent=50,
        grade_5_min_percent=70,
    )


@pytest.mark.parametrize(
    ("points", "maximum", "expected"),
    [(0, 25, 2), (7, 25, 2), (8, 25, 3), (12, 25, 3),
     (13, 25, 4), (17, 25, 4), (18, 25, 5), (25, 25, 5)],
)
def test_standard_scale_uses_percentage_without_rounding(points, maximum, expected):
    assert scale().grade_for(points, maximum) == expected


@pytest.mark.parametrize(
    "values",
    [
        {"grade_2_min_percent": 1, "grade_3_min_percent": 30, "grade_4_min_percent": 50, "grade_5_min_percent": 70},
        {"grade_2_min_percent": 0, "grade_3_min_percent": 50, "grade_4_min_percent": 50, "grade_5_min_percent": 70},
    ],
)
def test_grade_scale_rejects_invalid_thresholds(values):
    with pytest.raises(ValueError):
        GradeScale(**values)
