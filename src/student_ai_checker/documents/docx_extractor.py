"""Safe DOCX extraction into stable evidence items."""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Iterator
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from student_ai_checker.exceptions import DocumentExtractionError
from student_ai_checker.models import (
    EvidenceItem,
    EvidenceKind,
    ExtractedDocument,
    ExtractionIssue,
    ExtractionIssueSeverity,
)

SUPPORTED_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}


def sha256_file(path: Path) -> str:
    """Calculate SHA-256 without modifying the source file."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_body_blocks(document: DocumentObject) -> Iterator[Paragraph | Table]:
    """Yield top-level paragraphs and tables in document.xml order."""

    body = document.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _physical_cells(table: Table) -> Iterator[tuple[int, int, _Cell]]:
    """Yield physical OOXML cells, avoiding duplicate objects for merged cells."""

    for row_index, tr in enumerate(table._tbl.tr_lst, start=1):
        for cell_index, tc in enumerate(tr.tc_lst, start=1):
            yield row_index, cell_index, _Cell(tc, table)


def _image_blips(element: object) -> list[object]:
    return list(element.xpath(".//a:blip"))  # type: ignore[attr-defined]


class DocxExtractor:
    """Extract paragraphs, table cells and embedded PNG/JPEG images from DOCX."""

    def extract(self, source_path: str | Path) -> ExtractedDocument:
        path = Path(source_path)
        if path.suffix.lower() != ".docx":
            raise DocumentExtractionError("На первом этапе поддерживаются только файлы DOCX.")
        if not path.is_file():
            raise DocumentExtractionError(f"Файл не найден: {path}")

        source_hash = sha256_file(path)
        issues: list[ExtractionIssue] = []
        self._inspect_package(path, issues)

        try:
            document = Document(path)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise DocumentExtractionError(
                "DOCX повреждён или имеет неподдерживаемую структуру; автоматическая оценка не выполнялась."
            ) from exc
        except Exception as exc:
            raise DocumentExtractionError(
                "Не удалось безопасно разобрать DOCX; автоматическая оценка не выполнялась."
            ) from exc

        evidence: list[EvidenceItem] = []
        paragraph_number = 0
        table_number = 0
        image_number = 0
        order_index = 1

        for block in _iter_body_blocks(document):
            if isinstance(block, Paragraph):
                paragraph_number += 1
                paragraph_id = f"DOC1:P:{paragraph_number:04d}"
                text = block.text.strip()
                if text:
                    evidence.append(EvidenceItem(id=paragraph_id, kind=EvidenceKind.PARAGRAPH, order_index=order_index, text=text))
                    order_index += 1
                extracted, image_number, order_index = self._extract_images(element=block._p, document=document, image_number=image_number, order_index=order_index, parent_evidence_id=paragraph_id if text else None, issues=issues)
                evidence.extend(extracted)
                continue

            table_number += 1
            for row_number, cell_number, cell in _physical_cells(block):
                cell_id = f"DOC1:T:{table_number:04d}:R:{row_number:04d}:C:{cell_number:04d}"
                cell_text = "\n".join(paragraph.text.strip() for paragraph in cell.paragraphs if paragraph.text.strip())
                if cell_text:
                    evidence.append(EvidenceItem(id=cell_id, kind=EvidenceKind.TABLE_CELL, order_index=order_index, text=cell_text))
                    order_index += 1
                extracted, image_number, order_index = self._extract_images(element=cell._tc, document=document, image_number=image_number, order_index=order_index, parent_evidence_id=cell_id if cell_text else None, issues=issues)
                evidence.extend(extracted)
                if cell._tc.xpath(".//w:tbl"):
                    issues.append(ExtractionIssue(code="nested_table_detected", message="Обнаружена вложенная таблица. Её текст может быть представлен не с полной структурной точностью.", severity=ExtractionIssueSeverity.WARNING, affected_kind=EvidenceKind.TABLE_CELL))

        metadata = EvidenceItem(id="DOC1:META:0001", kind=EvidenceKind.DOCUMENT_METADATA, order_index=0, text=("DOCX parsed locally. " f"paragraph_elements={paragraph_number}; " f"table_elements={table_number}; " f"embedded_supported_images={image_number}; " f"extraction_issues={len(issues)}; " f"source_sha256={source_hash}"))
        evidence.insert(0, metadata)
        return ExtractedDocument(source_name=path.name, source_sha256=source_hash, evidence=evidence, issues=issues)

    def _extract_images(self, *, element: object, document: DocumentObject, image_number: int, order_index: int, parent_evidence_id: str | None, issues: list[ExtractionIssue]) -> tuple[list[EvidenceItem], int, int]:
        extracted: list[EvidenceItem] = []
        for blip in _image_blips(element):
            relation_id = blip.get(qn("r:embed"))  # type: ignore[attr-defined]
            linked_relation_id = blip.get(qn("r:link"))  # type: ignore[attr-defined]
            if not relation_id:
                issues.append(ExtractionIssue(code="external_or_unresolved_image", message="Обнаружено изображение без встроенных данных; внешние изображения не загружаются из сети.", severity=ExtractionIssueSeverity.ERROR, affected_kind=EvidenceKind.IMAGE))
                if linked_relation_id:
                    continue
                continue
            try:
                image_part = document.part.related_parts[relation_id]
                content_type = getattr(image_part, "content_type", "")
                blob = image_part.blob
            except Exception:
                issues.append(ExtractionIssue(code="image_relationship_error", message="Не удалось извлечь встроенное изображение из DOCX.", severity=ExtractionIssueSeverity.ERROR, affected_kind=EvidenceKind.IMAGE))
                continue
            if content_type not in SUPPORTED_IMAGE_CONTENT_TYPES:
                issues.append(ExtractionIssue(code="unsupported_image_format", message=f"Формат встроенного изображения пока не поддерживается: {content_type or 'unknown'}.", severity=ExtractionIssueSeverity.ERROR, affected_kind=EvidenceKind.IMAGE))
                continue
            image_number += 1
            image_id = f"DOC1:IMG:{image_number:04d}"
            extracted.append(EvidenceItem(id=image_id, kind=EvidenceKind.IMAGE, order_index=order_index, parent_evidence_id=parent_evidence_id, mime_type="image/jpeg" if content_type == "image/jpg" else content_type, sha256=hashlib.sha256(blob).hexdigest(), binary_data=blob))
            order_index += 1
        return extracted, image_number, order_index

    @staticmethod
    def _inspect_package(path: Path, issues: list[ExtractionIssue]) -> None:
        try:
            with zipfile.ZipFile(path) as package:
                names = set(package.namelist())
        except zipfile.BadZipFile as exc:
            raise DocumentExtractionError("DOCX повреждён и не является корректным OOXML-архивом.") from exc
        if any(name.startswith("word/embeddings/") for name in names):
            issues.append(ExtractionIssue(code="embedded_object_ignored", message="Встроенный OLE-объект обнаружен и намеренно не выполняется/не анализируется.", severity=ExtractionIssueSeverity.WARNING))
        if "word/vbaProject.bin" in names:
            issues.append(ExtractionIssue(code="macro_ignored", message="Макрос обнаружен и намеренно не выполняется.", severity=ExtractionIssueSeverity.WARNING))
