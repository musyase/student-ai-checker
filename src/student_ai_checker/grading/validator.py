"""Deterministic validation of AI-proposed grading results."""

from __future__ import annotations

import math

from student_ai_checker.exceptions import GradingValidationError
from student_ai_checker.models import CriteriaSet, ExtractedDocument, GradingResponse


class GradingValidator:
    """Enforce teacher-approved criteria and extractor-generated evidence IDs."""

    def validate(self, response: GradingResponse, *, criteria: CriteriaSet, document: ExtractedDocument) -> GradingResponse:
        expected = criteria.by_id()
        seen: set[str] = set()
        if len(response.results) != len(criteria.criteria):
            raise GradingValidationError("AI response must contain exactly one result for each approved criterion")
        for result in response.results:
            if result.criterion_id in seen:
                raise GradingValidationError(f"Duplicate criterion_id in AI response: {result.criterion_id}")
            seen.add(result.criterion_id)
            criterion = expected.get(result.criterion_id)
            if criterion is None:
                raise GradingValidationError(f"Unknown criterion_id returned by AI: {result.criterion_id}")
            if result.title != criterion.title:
                raise GradingValidationError(f"AI changed criterion title: {result.criterion_id}")
            if not math.isclose(result.max_score, criterion.max_score, abs_tol=1e-9):
                raise GradingValidationError(f"AI changed max_score: {result.criterion_id}")
            if result.proposed_score is not None and not any(math.isclose(result.proposed_score, allowed, abs_tol=1e-9) for allowed in criterion.allowed_scores):
                raise GradingValidationError(f"AI returned a score outside allowed_scores: {result.criterion_id}")
            unknown_evidence = set(result.evidence_ids) - document.evidence_ids
            if unknown_evidence:
                raise GradingValidationError("AI returned unknown evidence IDs: " + ", ".join(sorted(unknown_evidence)))
        missing = set(expected) - seen
        if missing:
            raise GradingValidationError("AI omitted approved criteria: " + ", ".join(sorted(missing)))
        return response
