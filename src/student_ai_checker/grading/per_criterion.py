"""Opt-in, sequential grading with one approved criterion per model request.

This changes task scope, not evidence selection, grading policy or model output.
The existing checker retains its template filtering, retry limit and validators.
"""

from __future__ import annotations

import logging

from student_ai_checker.exceptions import OllamaResponseError, StudentAICheckerError
from student_ai_checker.models import (
    CriteriaSet,
    CriterionResult,
    CriterionStatus,
    ExtractedDocument,
    GradingResponse,
)

from .checker import StudentWorkChecker
from .validator import GradingValidator

LOGGER = logging.getLogger(__name__)


class PerCriterionChecker:
    """Evaluate every criterion once, with at most the checker's one repair retry.

    All original evidence allowed by the wrapped checker is available each time.
    No previous model answer or diagnostic description enters a later request.
    A transport/validation failure aborts the run; no scores are fabricated and
    the CLI does not save a partly assembled response as a completed run.
    """

    def __init__(self, checker: StudentWorkChecker) -> None:
        self.checker = checker

    def check(
        self,
        document: ExtractedDocument,
        criteria: CriteriaSet,
        *,
        tolerate_failures: bool = False,
    ) -> GradingResponse:
        # Work from an independent snapshot; never alter the approved input set.
        snapshot = criteria.model_copy(deep=True)
        results: list[CriterionResult] = []
        count = len(snapshot.criteria)
        for index, criterion in enumerate(snapshot.criteria, start=1):
            LOGGER.info("Проверка критерия %d/%d: %s", index, count, criterion.id)
            single = CriteriaSet(
                version=snapshot.version,
                criteria=[criterion.model_copy(deep=True)],
            )
            try:
                response = self.checker.check(document, single)
            except StudentAICheckerError as exc:
                if tolerate_failures and isinstance(exc, OllamaResponseError) and document.evidence_ids:
                    LOGGER.warning(
                        "Критерий %s не удалось проверить после повторной попытки; "
                        "сохраняем его без балла для ручной проверки.",
                        criterion.id,
                    )
                    results.append(CriterionResult(
                        criterion_id=criterion.id,
                        title=criterion.title,
                        max_score=criterion.max_score,
                        proposed_score=None,
                        status=CriterionStatus.UNABLE_TO_CHECK,
                        explanation="Локальная модель не вернула проверяемый ответ по этому критерию.",
                        evidence_ids=[sorted(document.evidence_ids)[0]],
                        student_feedback="",
                        manual_review_required=True,
                        manual_review_reason="Критерий не удалось автоматически проверить; требуется решение преподавателя.",
                    ))
                    continue
                LOGGER.error(
                    "Проверка остановлена на критерии %d/%d; "
                    "ошибка не заменяется нулём, общий результат не сформирован.",
                    index, count,
                )
                raise
            results.extend(response.results)

        merged = GradingResponse(results=results)
        # Each item has already passed the wrapped template/extraction safeguards.
        # Check the assembled set again: no omitted, duplicated or changed criteria.
        return GradingValidator().validate(merged, criteria=snapshot, document=document)
