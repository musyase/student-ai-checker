"""Conservative element-level comparison with a teacher-selected blank DOCX.

Matching is not authorship detection. Candidate means changed/added, not correct.
Original evidence text, image bytes and IDs are never rewritten or renumbered.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from student_ai_checker.exceptions import DocumentExtractionError
from student_ai_checker.models import EvidenceItem, EvidenceKind, ExtractedDocument


@dataclass(frozen=True)
class TemplateComparison:
    source_sha256: str
    template_sha256: str
    origins: dict[str, str]
    filtered_document: ExtractedDocument

    def report(self) -> dict[str, object]:
        counts = {name: list(self.origins.values()).count(name) for name in sorted(set(self.origins.values()))}
        return {
            "algorithm": "element-comparison-v1",
            "template_sha256": self.template_sha256,
            "counts": counts,
            "evidence_origins": self.origins,
            "warning": "Отличие от шаблона не доказывает правильность или авторство ответа.",
        }


def _text(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFC", value or "").split())


def _image_hash(item: EvidenceItem) -> str:
    if not item.binary_data:
        raise DocumentExtractionError("Изображение не содержит извлечённых данных; нужна ручная проверка.")
    return hashlib.sha256(item.binary_data).hexdigest()


def _field_prefix(text: str) -> str | None:
    # Labels such as 'Название: [...]' or 'Ответ: 1 — ____'. Keep [] answers.
    match = re.search(r"\[|_{2,}", text)
    if match:
        prefix = text[:match.start()].strip()
        if len(prefix) >= 4:
            return prefix
    return None


def compare_template(document: ExtractedDocument, template: ExtractedDocument) -> TemplateComparison:
    """Filter exact template elements; flag near-edited prose instead of grading it."""
    # Any detected unsupported extraction makes negative conclusions unsafe.
    if template.issues or document.issues:
        raise DocumentExtractionError(
            "Есть предупреждения извлечения бланка или работы. "
            "Сопоставление не считается полным; нужна ручная проверка, баллы не выставлены."
        )
    originals = [_text(e.text) for e in template.evidence if e.text and e.kind is not EvidenceKind.DOCUMENT_METADATA]
    original_set = set(originals)
    if not original_set:
        raise DocumentExtractionError("Исходный бланк не содержит извлечённого текста для сравнения.")
    images = {_image_hash(e) for e in template.images}
    prefixes = [prefix for t in originals if (prefix := _field_prefix(t))]
    instructions = [t for t in originals if len(t) >= 40 and _field_prefix(t) is None]
    origins: dict[str, str] = {}
    kept: list[EvidenceItem] = []
    for item in document.evidence:
        if item.kind is EvidenceKind.DOCUMENT_METADATA:
            origin = "document_metadata"
        elif item.kind is EvidenceKind.IMAGE:
            origin = "template_match" if _image_hash(item) in images else "candidate"
        else:
            text = _text(item.text)
            if not text or text in original_set:
                origin = "template_match"
            elif any(text.startswith(prefix) for prefix in prefixes):
                origin = "candidate"
            elif any(
                SequenceMatcher(None, t, text, autojunk=False).ratio() >= 0.85
                for t in instructions
            ):
                origin = "ambiguous"
            else:
                origin = "candidate"
        origins[item.id] = origin
        if origin != "template_match":
            kept.append(item)
    if len(origins) != len(document.evidence):
        raise DocumentExtractionError("Повторяющиеся evidence ID; сопоставление остановлено.")
    # A separate view is passed to AI and validation. The original remains intact.
    filtered = document.model_copy(update={"evidence": kept})
    return TemplateComparison(document.source_sha256, template.source_sha256, origins, filtered)
