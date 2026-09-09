"""Real Ollama grading with deterministic exclusion of inherited template evidence."""

from __future__ import annotations

from student_ai_checker.ai import OllamaClient
from student_ai_checker.documents.template import TemplateComparison
from student_ai_checker.exceptions import GradingValidationError
from student_ai_checker.models import CriteriaSet, CriterionStatus, EvidenceKind, ExtractedDocument, GradingResponse

from .checker import StudentWorkChecker
from .validator import GradingValidator


class TemplateGradingValidator(GradingValidator):
    def __init__(self, comparison: TemplateComparison) -> None:
        self.comparison = comparison

    def validate(self, response: GradingResponse, *, criteria: CriteriaSet, document: ExtractedDocument) -> GradingResponse:
        super().validate(response, criteria=criteria, document=document)
        evidence = document.evidence_by_id()
        has_ambiguity = "ambiguous" in self.comparison.origins.values()
        for result in response.results:
            # A questionable template match may hide a real answer. Never penalise it.
            if has_ambiguity and result.proposed_score is not None:
                raise GradingValidationError(
                    "Template comparison is ambiguous: use insufficient_evidence, null score and manual review."
                )
            if result.proposed_score is not None and result.proposed_score > 0:
                candidate_ids = [i for i in result.evidence_ids if self.comparison.origins.get(i) == "candidate"]
                if not candidate_ids:
                    raise GradingValidationError("A positive score requires candidate answer evidence, not document metadata or template text.")
                criterion = criteria.by_id()[result.criterion_id]
                for requirement in criterion.evidence_requirements:
                    if requirement.required and requirement.kinds and not any(
                        evidence[i].kind in requirement.kinds for i in candidate_ids
                    ):
                        raise GradingValidationError("Positive score does not cite a required evidence kind; use manual review if uncertain.")
            if result.status is CriterionStatus.COMPLETED and result.proposed_score != result.max_score:
                raise GradingValidationError("completed requires max_score; use partially_completed for partial credit.")
            if result.status is CriterionStatus.NOT_COMPLETED and result.proposed_score != 0:
                raise GradingValidationError("not_completed requires zero score; technical uncertainty requires null and manual review.")
            if result.status is CriterionStatus.PARTIALLY_COMPLETED and not (0 < result.proposed_score < result.max_score):
                raise GradingValidationError("partially_completed requires a score strictly between zero and max_score.")
        return response


class TemplateWorkChecker(StudentWorkChecker):
    def __init__(self, ollama: OllamaClient, comparison: TemplateComparison) -> None:
        super().__init__(ollama, TemplateGradingValidator(comparison))
        self.comparison = comparison

    def check(self, document: ExtractedDocument, criteria: CriteriaSet) -> GradingResponse:
        if document.source_sha256 != self.comparison.source_sha256:
            raise GradingValidationError("Template comparison belongs to a different submission.")
        return super().check(self.comparison.filtered_document, criteria)

    def _build_system_prompt(self, criteria: CriteriaSet) -> str:
        warning = " Есть неоднозначные изменения: все затронутые результаты требуют ручной проверки без балла." if "ambiguous" in self.comparison.origins.values() else ""
        return super()._build_system_prompt(criteria) + """
TEMPLATE MODE:
Совпадающие с исходным бланком элементы и изображения исключены из evidence.
Оставшиеся элементы — только кандидаты в ответы, а не автоматически правильные решения.
Оценивай их содержание. Не засчитывай изменённую инструкцию, ФИО, дату или подпись
к рисунку вместо выполненного задания. Скобки вокруг заполненного ответа допустимы.
document_metadata подтверждает только разбор файла, но не выполнение критерия.
Для положительного балла нужна ссылка на содержательный ответ разрешённого типа.
При действительно отсутствующем ответе можно сослаться на DOC1:META:0001;
не используй это для положительного балла. Неизвестный/нечитаемый результат —
insufficient_evidence или unable_to_check, null, ручная проверка с причиной.
completed означает полный балл; partially_completed — промежуточный; not_completed — 0.
""" + warning
