"""Generation-schema compatibility and unchanged local checks.

HTTP replies are explicit mocks. These tests do NOT run Ollama's grammar parser
or verify a model's understanding of the synthetic text.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from student_ai_checker.ai import OllamaClient
from student_ai_checker.grading.checked_text import (
    COMPONENT_SCHEMA_VERSION, PARTS, PR1_TEMPLATE_SHA256, ComponentResponse,
    check_text_components, components_schema,
    verify_profile,
)
from student_ai_checker.models import CriteriaSet, EvidenceItem, ExtractedDocument


@pytest.fixture
def rubric():
    path = Path(__file__).parents[1] / "examples/pr1.criteria.approved.json"
    return CriteriaSet.model_validate_json(path.read_text(encoding="utf-8"))


def document(text="[Синтетический ответ для проверки передачи данных.]", *, blank=False):
    evidence = [EvidenceItem(id="DOC1:META:0001", kind="document_metadata", order_index=0, text="Parsed")]
    if not blank:
        evidence.append(EvidenceItem(id="DOC1:P:0002", kind="paragraph", order_index=1, text=text))
    return ExtractedDocument(source_name="synthetic.docx", source_sha256="a" * 64, evidence=evidence)


def response(parts, *, quote="Синтетический ответ", states=None):
    states = states or ["met"] * len(parts)
    return {"checks": [
        {"check_id": part.id, "verdict": states[i],
         "quotes": [{"evidence_id": "DOC1:P:0002", "quote": quote}],
         "reason": "Синтетическое решение для проверки кода."}
        for i, part in enumerate(parts)
    ]}


def run_mock(criterion, doc, replies):
    queue = iter(replies)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        reply = next(queue)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {
            "content": json.dumps(reply, ensure_ascii=False)
        }})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(base_url="http://127.0.0.1:11434", model="test-model", client=http)
        result, audit = check_text_components(client, criterion, PARTS[criterion.id], doc)
    return result, audit, sent


@pytest.mark.parametrize("criterion_id", list(PARTS))
@pytest.mark.parametrize("blank", [False, True])
def test_only_two_generation_bounds_change_and_models_remain_intact(criterion_id, blank):
    parts, doc = PARTS[criterion_id], document(blank=blank)
    original_model_schema = ComponentResponse.model_json_schema()
    before = copy.deepcopy(original_model_schema)
    expected = copy.deepcopy(original_model_schema)
    expected["properties"]["checks"].update(minItems=len(parts), maxItems=len(parts))
    expected["$defs"]["ComponentDecision"]["properties"]["check_id"]["enum"] = [p.id for p in parts]
    if not blank:
        expected["$defs"]["TextQuote"]["properties"]["evidence_id"]["enum"] = ["DOC1:P:0002"]
    assert expected["$defs"]["TextQuote"]["properties"]["quote"].pop("maxLength") == 6000
    assert expected["$defs"]["ComponentDecision"]["properties"]["reason"].pop("maxLength") == 2000
    actual = components_schema(parts, doc)
    assert actual == expected
    assert original_model_schema == before == ComponentResponse.model_json_schema()
    # No fallback to plain JSON, loss of fields, relaxed scores or extra properties.
    assert isinstance(actual, dict) and actual["additionalProperties"] is False
    assert actual["$defs"]["TextQuote"]["additionalProperties"] is False
    assert actual["$defs"]["ComponentDecision"]["properties"]["quotes"]["maxItems"] == 8
    if blank:
        assert "enum" not in actual["$defs"]["TextQuote"]["properties"]["evidence_id"]


@pytest.mark.parametrize("field,length,accepted", [
    ("quote", 6000, True), ("quote", 6001, False),
    ("reason", 2000, True), ("reason", 2001, False),
])
def test_original_local_length_limits_are_not_relaxed(field, length, accepted):
    raw = response(PARTS["pr1.add-button"])
    target = raw["checks"][0]["quotes"][0] if field == "quote" else raw["checks"][0]
    target[field] = "я" * length
    if accepted:
        parsed = ComponentResponse.model_validate_json(json.dumps(raw, ensure_ascii=False))
        value = parsed.checks[0].quotes[0].quote if field == "quote" else parsed.checks[0].reason
        assert value == "я" * length
    else:
        with pytest.raises(ValidationError):
            ComponentResponse.model_validate_json(json.dumps(raw, ensure_ascii=False))


@pytest.mark.parametrize("field,value", [
    ("quote", ""), ("quote", " \n\t"), ("quote", 7),
    ("reason", ""), ("reason", " \n\t"), ("reason", True),
])
def test_local_nonblank_and_type_checks_remain(field, value):
    raw = response(PARTS["pr1.add-button"])
    target = raw["checks"][0]["quotes"][0] if field == "quote" else raw["checks"][0]
    target[field] = value
    with pytest.raises(ValidationError):
        ComponentResponse.model_validate_json(json.dumps(raw))


def test_local_quote_count_limit_remains():
    raw = response(PARTS["pr1.add-button"])
    raw["checks"][0]["quotes"] *= 9
    with pytest.raises(ValidationError):
        ComponentResponse.model_validate(raw)


def test_long_original_text_and_maximum_quote_are_sent_without_truncation(rubric):
    c = rubric.by_id()["pr1.add-button"]
    quote = "я" * 6000
    doc = document("BEGIN " + quote + " END " + "other text " * 100)
    before = doc.model_dump_json()
    result, audit, sent = run_mock(c, doc, [response(PARTS[c.id], quote=quote)])
    assert result.proposed_score == 3 and len(sent) == 1
    request = sent[0]
    assert json.loads(request["messages"][1]["content"])["text_evidence"][0]["text"] == doc.evidence[1].text
    assert "maxLength" not in json.dumps(request["format"])
    assert request["options"] == {"temperature": 0, "num_ctx": 16384}
    assert request["think"] is False and request["stream"] is False
    assert doc.model_dump_json() == before
    assert audit["components"][0]["quotes"][0]["quote"] == quote
    assert audit["generation_schema_version"] == COMPONENT_SCHEMA_VERSION == 2
    digest = hashlib.sha256(json.dumps(request["format"], ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    assert audit["generation_schema_sha256"] == digest
    assert ComponentResponse.model_json_schema()["$defs"]["TextQuote"]["properties"]["quote"]["maxLength"] == 6000


@pytest.mark.parametrize("field,length", [("quote", 6001), ("reason", 2001)])
def test_oversized_model_reply_is_rejected_not_trimmed_or_graded(rubric, field, length):
    c = rubric.by_id()["pr1.add-button"]
    raw = response(PARTS[c.id])
    target = raw["checks"][0]["quotes"][0] if field == "quote" else raw["checks"][0]
    target[field] = "я" * length
    result, audit, sent = run_mock(c, document("я" * 7000), [raw, raw])
    assert result.proposed_score is None and result.status.value == "unable_to_check"
    assert result.manual_review_required and result.student_feedback == ""
    assert len(sent) == 2 and audit["validation_errors"] == ["ValidationError", "ValidationError"]
    assert sent[0]["format"] == sent[1]["format"]
    saved = json.loads(audit["attempts"][0])["checks"][0]
    assert len(saved["quotes"][0]["quote"] if field == "quote" else saved["reason"]) == length
    assert audit["components"] == []


@pytest.mark.parametrize("mutation", ["extra_score", "extra_check", "duplicate", "forged_quote", "metadata", "empty_quotes"])
def test_safeguards_still_reject_invalid_replies_after_schema_change(rubric, mutation):
    c = rubric.by_id()["pr1.add-button"]
    raw = response(PARTS[c.id])
    if mutation == "extra_score": raw["proposed_score"] = 3
    elif mutation == "extra_check": raw["checks"].append({**raw["checks"][0], "check_id": "screenshot_required"})
    elif mutation == "duplicate": raw["checks"][1] = copy.deepcopy(raw["checks"][0])
    elif mutation == "forged_quote": raw["checks"][0]["quotes"][0]["quote"] = "invented text"
    elif mutation == "metadata": raw["checks"][0]["quotes"][0] = {"evidence_id": "DOC1:META:0001", "quote": "Parsed"}
    else: raw["checks"][0]["quotes"] = []
    result, audit, sent = run_mock(c, document(), [raw, raw])
    assert result.proposed_score is None and result.status.value == "unable_to_check"
    assert len(sent) == 2 and len(audit["validation_errors"]) == 2


@pytest.mark.parametrize("states,score", [(["met"] * 3, 3), (["met", "not_met", "met"], 2),
                                         (["not_met"] * 3, 0), (["met", "uncertain", "met"], None)])
def test_arithmetic_and_valid_uncertainty_unchanged(rubric, states, score):
    c = rubric.by_id()["pr1.add-button"]
    before = rubric.model_dump_json()
    profile = verify_profile(rubric, PR1_TEMPLATE_SHA256)
    result, audit, sent = run_mock(c, document(), [response(PARTS[c.id], states=states)])
    assert result.proposed_score == score and len(sent) == 1
    assert len(audit["components"]) == 3
    assert result.manual_review_required == (score is None)
    assert rubric.model_dump_json() == before and verify_profile(rubric, PR1_TEMPLATE_SHA256) == profile


def test_reported_grammar_error_is_still_single_attempt_without_fallback(rubric):
    c = rubric.by_id()["pr1.add-button"]
    error = json.dumps({"error": {"code": 400, "message":
        "Failed to initialize samplers: failed to parse grammar", "type": "invalid_request_error"}})
    result, audit, sent = run_mock(c, document(), [httpx.Response(400, json={"error": error})])
    assert result.proposed_score is None and result.status.value == "unable_to_check"
    assert len(sent) == 1 and isinstance(sent[0]["format"], dict)
    assert audit["attempts"] == [] and "failed to parse grammar" in audit["error"]
    assert audit["generation_schema_version"] == 2


def test_empty_candidate_set_no_empty_enum_or_fake_positive(rubric):
    c = rubric.by_id()["pr1.add-button"]
    raw = response(PARTS[c.id], states=["not_met"] * 3)
    for item in raw["checks"]: item["quotes"] = []
    result, audit, sent = run_mock(c, document(blank=True), [raw])
    assert result.proposed_score == 0 and len(sent) == 1
    assert json.loads(sent[0]["messages"][1]["content"])["text_evidence"] == []
    assert "enum" not in sent[0]["format"]["$defs"]["TextQuote"]["properties"]["evidence_id"]
