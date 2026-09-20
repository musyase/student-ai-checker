"""Opt-in PR1 pilot: fixed text components, matching by code, existing visual v3.

This increment does not claim that all semantic decisions have become deterministic.
"""
from __future__ import annotations

import hashlib
import logging
import copy
from dataclasses import replace
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

    def _visual(self, comparison=None):
        comparison = comparison or self.comparison
        if self._visual_factory is not None:
            return self._visual_factory(self.ollama, comparison)
        from .visual_review import VisualReviewChecker
        return VisualReviewChecker(self.ollama, comparison)

    def check(self, original: ExtractedDocument, criteria: CriteriaSet) -> GradingResponse:
        self.audit = []
        self.profile_digest = None
        if self.comparison is None:
            raise GradingValidationError("checked-scoring требует исходный бланк и запись утверждения.")
        self.profile_digest = verify_profile(criteria, self.comparison.template_sha256)
        if original.source_sha256 != self.comparison.source_sha256:
            raise GradingValidationError("Сопоставление относится к другой работе.")
        ambiguous_ids = {
            evidence_id for evidence_id, origin in self.comparison.origins.items()
            if origin == "ambiguous"
        }
        scoring_origins = {
            evidence_id: origin
            for evidence_id, origin in self.comparison.origins.items()
            if origin != "ambiguous"
        }
        document = self.comparison.filtered_document.model_copy(update={
            "evidence": [
                evidence for evidence in self.comparison.filtered_document.evidence
                if evidence.id not in ambiguous_ids
            ]
        })
        try:
            scoring_comparison = replace(
                self.comparison,
                origins=scoring_origins,
                filtered_document=document,
            )
        except TypeError:
            # Test doubles and connector adapters may expose the same small
            # comparison protocol without using the production dataclass.
            scoring_comparison = copy.copy(self.comparison)
            scoring_comparison.origins = scoring_origins
            scoring_comparison.filtered_document = document
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
        self.ollama.assert_ready()
        results = []
        for index, criterion in enumerate(snapshot.criteria, 1):
            LOGGER.info("Проверяемый расчёт: критерий %d/%d: %s", index, len(snapshot.criteria), criterion.id)
            if criterion.id == "pr1.terms":
                result, trace = check_matching(criterion, original, scoring_comparison)
            elif criterion.id in PARTS:
                result, trace = check_text_components(
                    self.ollama,
                    criterion,
                    PARTS[criterion.id],
                    document,
                    empty_source_is_proven_absence=True,
                    resolve_uncertain=True,
                )
            else:
                visual = self._visual(scoring_comparison)
                single = CriteriaSet(version=snapshot.version, criteria=[criterion])
                result = visual.check(original, single).results[0]
                trace = {"criterion_id": criterion.id, "method": "existing_visual_v3",
                         "model_output_is_untrusted": True, "visual_audit": visual.audit}
            if ambiguous_ids:
                trace["excluded_ambiguous_evidence_ids"] = sorted(ambiguous_ids)
            trace["scoring_version"] = CHECKED_SCORING_VERSION
            self.audit.append(trace)
            results.append(result)
        response = GradingResponse(results=results)
        return GradingValidator().validate(response, criteria=snapshot, document=document)
