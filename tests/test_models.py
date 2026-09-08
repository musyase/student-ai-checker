from __future__ import annotations

import pytest
from pydantic import ValidationError

from student_ai_checker.models import CriteriaSet, Criterion, CriterionResult, CriterionStatus, EvidenceRequirement


def criterion() -> Criterion:
    return Criterion(id="c1", title="Критерий", description="Описание", max_score=2, allowed_scores=[0, 1, 2], evidence_requirements=[EvidenceRequirement(description="Текст", kinds=["paragraph"])])


def test_criterion_accepts_valid_allowed_scores() -> None:
    assert criterion().allowed_scores == [0.0, 1.0, 2.0]


def test_criterion_rejects_score_above_max() -> None:
    with pytest.raises(ValidationError):
        Criterion(id="c1", title="Критерий", description="Описание", max_score=2, allowed_scores=[0, 3])


def test_criterion_requires_max_score_in_allowed_scores() -> None:
    with pytest.raises(ValidationError):
        Criterion(id="c1", title="Критерий", description="Описание", max_score=2, allowed_scores=[0, 1])


def test_criteria_set_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        CriteriaSet(criteria=[criterion(), criterion()])


def test_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        CriterionResult(criterion_id="c1", title="Критерий", max_score=2, proposed_score=2, status="almost_done", explanation="Текст", evidence_ids=["DOC1:P:0001"])


def test_unresolved_result_cannot_have_zero_score() -> None:
    with pytest.raises(ValidationError):
        CriterionResult(criterion_id="c1", title="Критерий", max_score=2, proposed_score=0, status=CriterionStatus.UNABLE_TO_CHECK, explanation="Ошибка извлечения", evidence_ids=["DOC1:META:0001"], manual_review_required=True, manual_review_reason="Ошибка")


def test_unresolved_result_requires_manual_review_reason() -> None:
    with pytest.raises(ValidationError):
        CriterionResult(criterion_id="c1", title="Критерий", max_score=2, proposed_score=None, status=CriterionStatus.INSUFFICIENT_EVIDENCE, explanation="Неясно", evidence_ids=["DOC1:P:0001"], manual_review_required=True)
