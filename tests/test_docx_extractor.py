from __future__ import annotations

import hashlib
from pathlib import Path

from student_ai_checker.documents import DocxExtractor, sha256_file
from student_ai_checker.models import EvidenceKind


def test_sha256_file_with_cyrillic_path(tmp_path: Path) -> None:
    path = tmp_path / "работа студента.docx"
    path.write_bytes(b"abc")
    assert sha256_file(path) == hashlib.sha256(b"abc").hexdigest()


def test_extracts_text_tables_and_embedded_png(sample_docx: Path) -> None:
    extracted = DocxExtractor().extract(sample_docx)
    assert extracted.source_name == "Работа Иванов.docx"
    assert extracted.source_sha256 == sha256_file(sample_docx)
    assert "DOC1:META:0001" in extracted.evidence_ids
    texts = [item.text for item in extracted.evidence if item.text]
    assert "Установил Android Studio и создал проект." in texts
    assert "Проблема" in texts
    assert "SDK не найден — установил через SDK Manager." in texts
    images = extracted.images
    assert len(images) == 1
    assert images[0].id == "DOC1:IMG:0001"
    assert images[0].kind is EvidenceKind.IMAGE
    assert images[0].mime_type == "image/png"
    assert images[0].binary_data
    assert images[0].sha256 == hashlib.sha256(images[0].binary_data).hexdigest()


def test_evidence_ids_are_stable_for_same_file(sample_docx: Path) -> None:
    extractor = DocxExtractor()
    first = extractor.extract(sample_docx)
    second = extractor.extract(sample_docx)
    assert [item.id for item in first.evidence] == [item.id for item in second.evidence]


def test_table_cell_evidence_has_structured_ids(sample_docx: Path) -> None:
    extracted = DocxExtractor().extract(sample_docx)
    table_ids = [item.id for item in extracted.evidence if item.kind is EvidenceKind.TABLE_CELL]
    assert table_ids == ["DOC1:T:0001:R:0001:C:0001", "DOC1:T:0001:R:0001:C:0002"]
