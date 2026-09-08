"""Models for source evidence extracted from student documents."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EvidenceKind(str, Enum):
    PARAGRAPH = "paragraph"
    TABLE_CELL = "table_cell"
    IMAGE = "image"
    DOCUMENT_METADATA = "document_metadata"


class ExtractionIssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class EvidenceItem(BaseModel):
    """A trusted, extractor-generated evidence item."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    kind: EvidenceKind
    order_index: int = Field(ge=0)
    text: str | None = None
    parent_evidence_id: str | None = None
    mime_type: str | None = None
    sha256: str | None = None
    binary_data: bytes | None = Field(default=None, exclude=True, repr=False)


class ExtractionIssue(BaseModel):
    code: str
    message: str
    severity: ExtractionIssueSeverity
    affected_kind: EvidenceKind | None = None


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_name: str
    source_sha256: str
    evidence: list[EvidenceItem]
    issues: list[ExtractionIssue] = Field(default_factory=list)

    @property
    def evidence_ids(self) -> set[str]:
        return {item.id for item in self.evidence}

    @property
    def images(self) -> list[EvidenceItem]:
        return [item for item in self.evidence if item.kind is EvidenceKind.IMAGE]

    def evidence_by_id(self) -> dict[str, EvidenceItem]:
        return {item.id: item for item in self.evidence}
