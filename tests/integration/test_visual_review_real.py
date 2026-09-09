"""Opt-in semantic check against REAL local Ollama, including a negative control.

These are synthetic coloured images, not student work. A response containing only
null scores must not pass. Passing these cases does not establish grading quality.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import struct
import zlib

import pytest
from docx import Document

from student_ai_checker.ai import OllamaClient
from student_ai_checker.config import Settings
from student_ai_checker.documents import DocxExtractor
from student_ai_checker.documents.template import compare_template
from student_ai_checker.grading.visual_review import VISUAL_PIPELINE_VERSION, VisualReviewChecker
from student_ai_checker.models import CriteriaSet, Criterion

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("STUDENT_AI_CHECKER_RUN_OLLAMA") != "1",
                       reason="Requires explicit opt-in and real local Ollama"),
]


def _solid_png(rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    header = struct.pack("!2I5B", 256, 192, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\0" + bytes(rgb) * 256 for _ in range(192))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def _write_control_report(directory: Path, cases: list[dict]) -> Path:
    """Local synthetic diagnostics only; expected scores never enter model prompts."""
    directory.mkdir(parents=True, exist_ok=True)
    name = "visual-controls-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8] + ".json"
    path = directory / name
    payload = {
        "purpose": "synthetic_visual_controls_not_student_grading",
        "pipeline_version": VISUAL_PIPELINE_VERSION,
        "model_output_is_untrusted": True,
        "cases": cases,
    }
    # Do not overwrite earlier runs, including a rare filename collision.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return path


@pytest.fixture(scope="module")
def visual_audit(tmp_path_factory):
    configured = os.getenv("STUDENT_AI_CHECKER_TEST_ARTIFACT_DIR")
    directory = Path(configured) if configured else tmp_path_factory.mktemp("visual-control-audit")
    cases: list[dict] = []
    try:
        yield cases
    finally:
        report = _write_control_report(directory, cases)
        print(f"\nКонтрольный журнал: {report}")


@pytest.mark.parametrize("colour,expected", [("красным", 1), ("зелёным", 0)], ids=["present_feature", "absent_feature"])
def test_real_visual_review_present_and_absent_feature(tmp_path, colour, expected, visual_audit):
    blank = Document()
    blank.add_paragraph("Задание: приложите изображения. Требования задаются отдельно.")
    blank_path = tmp_path / "template.docx"
    blank.save(blank_path)
    for index, rgb in enumerate(((255, 0, 0), (0, 0, 255))):
        path = tmp_path / f"image-{index}.png"
        path.write_bytes(_solid_png(rgb))
        blank.add_picture(str(path))
    work_path = tmp_path / "work.docx"
    blank.save(work_path)
    extractor = DocxExtractor()
    doc = extractor.extract(work_path)
    comp = compare_template(doc, extractor.extract(blank_path))
    rubric = CriteriaSet(criteria=[Criterion(
        id="colour-check", title="Проверка цвета изображения",
        description=f"1 балл, если есть изображение, полностью залитое {colour} цветом. "
                    "0 баллов, если все изображения хорошо видны, но такого цвета нет. "
                    "Прочие цвета не засчитываются. По тексту и имени файла цвет не определять.",
        max_score=1, allowed_scores=[0, 1],
        evidence_requirements=[{"description": "Цвет самого изображения", "kinds": ["image"]}],
    )])
    settings = Settings.from_env()
    record = {
        "case": "present_feature" if expected else "absent_feature",
        "expected_score": expected, "model": settings.ollama_model,
        "num_ctx": settings.ollama_num_ctx, "source_sha256": doc.source_sha256,
        "result": None, "audit": [], "passed": False,
    }
    visual_audit.append(record)
    checker = None
    try:
        with OllamaClient(base_url=settings.ollama_base_url, model=settings.ollama_model,
                          timeout_seconds=settings.ollama_timeout_seconds,
                          num_ctx=settings.ollama_num_ctx) as client:
            checker = VisualReviewChecker(client, comp)
            result = checker.check(doc, rubric).results[0]
        record["result"] = result.model_dump(mode="json")
        record["audit"] = checker.audit
        details = json.dumps(record, ensure_ascii=False, indent=2)
        # Keep the original semantic assertions. Do not accept null to make tests pass.
        assert result.proposed_score == expected, details
        if expected:
            assert doc.images[0].id in result.evidence_ids, details
        assert len(checker.audit[0]["image_reviews"]) == 2, details
        assert all(r["finding"] is not None for r in checker.audit[0]["image_reviews"]), details
        record["passed"] = True
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        raise
    finally:
        if checker is not None:
            record["audit"] = checker.audit
