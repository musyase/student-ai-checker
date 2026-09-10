"""Opt-in PR1 pilot: fixed text components, matching by code, existing visual v3.

This increment does not claim that all semantic decisions have become deterministic.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable

from student_ai_checker.exceptions import DocumentExtractionError, GradingValidationError
from student_ai_checker.models import CriteriaSet, ExtractedDocument, GradingResponse
from .checked_text import (
    CHECKED_SCORING_VERSION, PARTS, _result, check_matching,
    verify_profile,
)
from .referenced_text import check_text_references as check_text_components
from .validator import GradingValidator

LOGGER = logging.getLogger(__name__)


class CheckedScoringChecker:
    def __init__(self, ollama, comparison, *, visual_factory: Callable | None = None):
        self.ollama = ollama
        self.comparison = comparison
        self._visual_factory = visual_factory
        self.audit: list[dict[str, Any]] = []
        self.profile_digest: str | None = None

    def _visual(self):
        if self._visual_factory is not None:
            return self._visual_factory(self.ollama, self.comparison)
        from .visual_review import VisualReviewChecker
        return VisualReviewChecker(self.ollama, self.comparison)

    def check(self, original: ExtractedDocument, criteria: CriteriaSet) -> GradingResponse:
        self.audit = []
        self.profile_digest = None
        if self.comparison is None:
            raise GradingValidationError("checked-scoring требует исходный бланк и запись утверждения.")
        self.profile_digest = verify_profile(criteria, self.comparison.template_sha256)
        if original.source_sha256 != self.comparison.source_sha256:
            raise GradingValidationError("Сопоставление относится к другой работе.")
        document = self.comparison.filtered_document
        if original.issues or document.issues:
            raise DocumentExtractionError("Разбор неполон; checked-scoring остановлен без выставления баллов.")
        if (len(original.evidence_ids) != len(original.evidence)
                or len(document.evidence_ids) != len(document.evidence)
                or "DOC1:META:0001" not in document.evidence_ids):
            raise DocumentExtractionError("Нарушена структура доказательств.")
        for image in document.images:
            if not image.binary_data or (image.sha256 and image.sha256 != hashlib.sha256(image.binary_data).hexdigest()):
                raise DocumentExtractionError("Не удалось подтвердить исходные байты изображения.")
        snapshot = criteria.model_copy(deep=True)
        if "ambiguous" in self.comparison.origins.values():
            # No prompt is allowed to turn uncertain template provenance into a grade.
            return GradingResponse(results=[_result(c, None, "Неоднозначное сопоставление с бланком; нужна ручная проверка.", []) for c in snapshot.criteria])
        self.ollama.assert_ready()
        results = []
        for index, criterion in enumerate(snapshot.criteria, 1):
            LOGGER.info("Проверяемый расчёт: критерий %d/%d: %s", index, len(snapshot.criteria), criterion.id)
            if criterion.id == "pr1.terms":
                result, trace = check_matching(criterion, original, self.comparison)
            elif criterion.id in PARTS:
                result, trace = check_text_components(self.ollama, criterion, PARTS[criterion.id], document)
                # Do not reject a permitted image-only answer after checking only text.
                # Full textual credit needs no screenshot. A non-full text result with
                # possible visual evidence remains unresolved in this first increment.
                if (criterion.id == "pr1.add-button" and document.images
                        and result.proposed_score is not None and result.proposed_score < criterion.max_score):
                    trace["text_route_result"] = result.model_dump(mode="json")
                    trace["policy_guard"] = "visual_alternative_not_evaluated"
                    result = _result(criterion, None,
                        "Текстовые пункты проверены, но доступна разрешённая визуальная альтернатива; её должен сверить преподаватель. Отсутствие снимка не является штрафом.",
                        result.evidence_ids)
            else:
                visual = self._visual()
                single = CriteriaSet(version=snapshot.version, criteria=[criterion])
                result = visual.check(original, single).results[0]
                trace = {"criterion_id": criterion.id, "method": "existing_visual_v3",
                         "model_output_is_untrusted": True, "visual_audit": visual.audit}
                # The legacy visual synthesis is not trusted to infer complete absence
                # of installation confirmation from an existing but indirect source.
                if criterion.id == "pr1.installation-evidence" and result.proposed_score is not None and result.proposed_score < criterion.max_score:
                    trace["visual_proposal"] = result.model_dump(mode="json")
                    trace["policy_guard"] = "installation_confirmation_needs_teacher"
                    result = _result(criterion, None,
                        "Визуальный конвейер не подтвердил полный балл. Нужно проверить допустимость снимка или текстовой альтернативы; автоматическое снижение по этому пункту пока заблокировано.",
                        result.evidence_ids)
            trace["scoring_version"] = CHECKED_SCORING_VERSION
            self.audit.append(trace)
            results.append(result)
        response = GradingResponse(results=results)
        return GradingValidator().validate(response, criteria=snapshot, document=document)
