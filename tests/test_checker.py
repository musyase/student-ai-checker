from __future__ import annotations

import json

from student_ai_checker.documents import DocxExtractor
from student_ai_checker.grading import StudentWorkChecker
from student_ai_checker.models import CriteriaSet, Criterion, EvidenceRequirement


class OllamaStub:
    """Test-only mock. Production code always uses the real OllamaClient."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def assert_ready(self) -> None:
        return None

    def chat_structured(self, **_: object) -> str:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _criteria() -> CriteriaSet:
    return CriteriaSet(criteria=[Criterion(id="c1", title="Проблема и решение", description="Описаны проблема и решение", max_score=2, allowed_scores=[0, 1, 2], evidence_requirements=[EvidenceRequirement(description="Текст", kinds=["table_cell", "paragraph"])])])


def _valid_response(evidence_id: str) -> str:
    return json.dumps({"results": [{"criterion_id": "c1", "title": "Проблема и решение", "max_score": 2, "proposed_score": 2, "status": "completed", "explanation": "Проблема и решение описаны.", "evidence_ids": [evidence_id], "student_feedback": "", "manual_review_required": False, "manual_review_reason": None}]}, ensure_ascii=False)


def test_checker_retries_once_after_unknown_evidence(sample_docx) -> None:
    document = DocxExtractor().extract(sample_docx)
    valid_id = "DOC1:T:0001:R:0001:C:0002"
    stub = OllamaStub([_valid_response("DOC1:P:9999"), _valid_response(valid_id)])
    response = StudentWorkChecker(stub).check(document, _criteria())  # type: ignore[arg-type]
    assert stub.calls == 2
    assert response.results[0].evidence_ids == [valid_id]


def test_image_extraction_error_cannot_become_automatic_zero(sample_docx) -> None:
    from student_ai_checker.models import EvidenceKind, ExtractionIssue, ExtractionIssueSeverity

    document = DocxExtractor().extract(sample_docx)
    document.issues.append(ExtractionIssue(code="image_relationship_error", message="Не удалось извлечь одно изображение", severity=ExtractionIssueSeverity.ERROR, affected_kind=EvidenceKind.IMAGE))
    criteria = CriteriaSet(criteria=[Criterion(id="image-required", title="Скриншот", description="Нужен скриншот", max_score=2, allowed_scores=[0, 1, 2], evidence_requirements=[EvidenceRequirement(description="Скриншот", kinds=["image"], required=True)])])
    model_answer = json.dumps({"results": [{"criterion_id": "image-required", "title": "Скриншот", "max_score": 2, "proposed_score": 0, "status": "not_completed", "explanation": "Скриншот не найден.", "evidence_ids": ["DOC1:META:0001"], "student_feedback": "Добавьте скриншот.", "manual_review_required": False, "manual_review_reason": None}]}, ensure_ascii=False)
    stub = OllamaStub([model_answer])
    response = StudentWorkChecker(stub).check(document, criteria)  # type: ignore[arg-type]
    result = response.results[0]
    assert result.status.value == "unable_to_check"
    assert result.proposed_score is None
    assert result.manual_review_required is True
    assert result.student_feedback == ""
    assert result.evidence_ids == ["DOC1:META:0001"]
