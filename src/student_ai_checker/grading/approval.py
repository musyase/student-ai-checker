"""Verify the immutable rubric/template snapshot used for a grading run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from student_ai_checker.exceptions import StudentAICheckerError
from student_ai_checker.grading.grade_scale import GradeScale
from student_ai_checker.models import CriteriaSet


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["approved"]
    approved_at: AwareDatetime
    approved_by: Literal["teacher", "automatic"]
    approval_mode: Literal["manual", "automatic"] = "manual"
    digest_format: Literal["criteria-model-json-v1"]
    criteria_version: int = Field(ge=1)
    criteria_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    template_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    grade_scale: GradeScale = Field(default_factory=GradeScale.standard)

    @field_validator("grade_scale", mode="before")
    @classmethod
    def use_standard_scale_when_omitted(cls, value):
        return GradeScale.standard() if value is None else value


def criteria_digest(criteria: CriteriaSet) -> str:
    """Digest validated values, insensitive to file whitespace and CRLF/LF."""
    content = json.dumps(
        criteria.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def verify_approval(
    path: Path, *, criteria: CriteriaSet, template_sha256: str,
) -> ApprovalRecord:
    """Check integrity; this is not an electronic signature or identity proof."""
    try:
        record = ApprovalRecord.model_validate_json(path.read_text(encoding="utf-8-sig"))
        digest = criteria_digest(criteria)
    except (OSError, ValueError, ValidationError) as exc:
        raise StudentAICheckerError("Не удалось прочитать корректную запись утверждения критериев.") from exc
    if record.criteria_version != criteria.version or record.criteria_sha256 != digest:
        raise StudentAICheckerError("Критерии изменились после утверждения. Требуется новая утверждённая версия.")
    if record.template_sha256 != template_sha256:
        raise StudentAICheckerError("Выбран другой или изменённый бланк. Нужен исходный шаблон утверждённой версии.")
    return record
