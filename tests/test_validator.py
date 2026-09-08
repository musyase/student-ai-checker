from __future__ import annotations

import pytest

from student_ai_checker.documents import DocxExtractor
from student_ai_checker.exceptions import GradingValidationError
from student_ai_checker.grading import GradingValidator
from student_ai_checker.models import CriteriaSet, Criterion, CriterionResult, CriterionStatus, GradingResponse


def _criteria() -> CriteriaSet:
    return CriteriaSet(criteria=[Criterion(id="c1", title="Описание", description="Найти описание", max_score=2, allowed_scores=[0, 1, 2])])


def _result(**updates) -> CriterionResult:
    data = dict(criterion_id="c1", title="Описание", max_score=2, proposed_score=2, status=CriterionStatus.COMPLETED, explanation="Есть описание", evidence_ids=["DOC1:P:0001"], student_feedback="", manual_review_required=False)
    data.update(updates)
    return CriterionResult(**data)


def test_validator_accepts_known_evidence(sample_docx) -> None:
    document = DocxExtractor().extract(sample_docx)
    response = GradingResponse(results=[_result()])
    assert GradingValidator().validate(response, criteria=_criteria(), document=document) is response


def test_validator_rejects_unknown_evidence_id(sample_docx) -> None:
    document = DocxExtractor().extract(sample_docx)
    response = GradingResponse(results=[_result(evidence_ids=["DOC1:IMG:9999"])])
    with pytest.raises(GradingValidationError, match="unknown evidence"):
        GradingValidator().validate(response, criteria=_criteria(), document=document)


def test_validator_rejects_disallowed_score(sample_docx) -> None:
    document = DocxExtractor().extract(sample_docx)
    response = GradingResponse(results=[_result(proposed_score=1.5)])
    with pytest.raises(GradingValidationError, match="allowed_scores"):
        GradingValidator().validate(response, criteria=_criteria(), document=document)


def test_validator_rejects_changed_max_score(sample_docx) -> None:
    document = DocxExtractor().extract(sample_docx)
    response = GradingResponse(results=[_result(max_score=3)])
    with pytest.raises(GradingValidationError, match="max_score"):
        GradingValidator().validate(response, criteria=_criteria(), document=document)
