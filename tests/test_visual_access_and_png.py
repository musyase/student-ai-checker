"""Portability and v3 regressions. HTTP replies below are mocks, not model inference."""
from __future__ import annotations

import copy
import hashlib
import json
import struct
import zlib

import pytest
from pydantic import ValidationError

from student_ai_checker.grading.visual_review import ImageFinding, ImageUncertainty, VisualReviewChecker
from student_ai_checker.models import CriteriaSet, CriterionStatus
from png_fixture_checks import assert_solid_fixture
from test_visual_review import source, criterion, finding, answer, transport, posts
from test_visual_review_contract import _load_control_module, limitation


def test_wire_contract_observes_before_assessing_visibility():
    props = ImageFinding.model_json_schema()['properties']
    assert 'readability' not in props
    assert props['visual_access']['enum'] == ['clear', 'limited', 'unavailable']
    assert list(props).index('observations') < list(props).index('visual_access')
    assert set(ImageUncertainty.model_json_schema()['required']) == {'detail', 'cause', 'impact'}


@pytest.mark.parametrize('old_access', ['readable', 'partly_readable', 'unreadable'])
def test_v2_findings_are_not_silently_reinterpreted(old_access):
    value = finding()
    del value['visual_access']
    value['readability'] = old_access
    with pytest.raises(ValidationError):
        ImageFinding.model_validate_json(json.dumps(value))


@pytest.mark.parametrize('cause', ['', ' \n\t', None, True, 123, 'feature', ' FEATURE. '])
def test_missing_blank_or_tautological_cause_is_invalid(cause):
    with pytest.raises(ValidationError):
        ImageUncertainty.model_validate({'detail': 'Feature', 'cause': cause, 'impact': 'blocking'})


def test_vague_blocker_cannot_be_accepted_as_a_complete_explanation():
    # The old log only named a feature. v3 requires a reason, not an auto-corrected flag.
    bad = finding(visual_access='unavailable', uncertainties=[{'detail': 'Feature', 'impact': 'blocking'}])
    with pytest.raises(ValidationError, match='cause'):
        ImageFinding.model_validate(bad)


@pytest.mark.parametrize('access', ['limited', 'unavailable'])
@pytest.mark.parametrize('score,status', [(2, 'completed'), (0, 'not_completed')])
def test_real_visibility_limit_blocks_both_positive_and_negative_scores(source, access, score, status):
    c = criterion()
    blocker = limitation('blocking', 'Required label is hidden')
    limited = finding(visual_access=access, uncertainties=[blocker])
    client, http, calls = transport([limited, finding(), answer(c, [i.id for i in source[2].images], score=score, status=status)])
    with http:
        checker = VisualReviewChecker(client, source[3])
        result = checker.check(source[2], CriteriaSet(criteria=[c])).results[0]
    assert result.proposed_score is None
    assert result.status is CriterionStatus.INSUFFICIENT_EVIDENCE
    trace = checker.audit[0]['guard_decisions'][0]
    assert trace['visual_access'] == access
    assert trace['blocking_causes'] == [blocker['cause']]
    assert len(posts(calls)) == 3


def test_invalid_visibility_report_does_not_trigger_retry_until_score(source):
    c = criterion()
    bad = finding(visual_access='unavailable', uncertainties=[{'detail': 'Feature', 'impact': 'blocking'}])
    client, http, calls = transport([bad, bad, finding()])
    with http:
        checker = VisualReviewChecker(client, source[3])
        result = checker.check(source[2], CriteriaSet(criteria=[c])).results[0]
    assert result.proposed_score is None and result.status is CriterionStatus.UNABLE_TO_CHECK
    assert checker.audit[0]['synthesis_attempts'] == []
    assert len(posts(calls)) == 3
    attempts = checker.audit[0]['image_reviews'][0]['attempts']
    assert len(attempts) == 2
    assert 'cause' not in json.loads(attempts[0])['uncertainties'][0]  # Not filled by application.


def test_schema_repair_keeps_original_pixels_and_never_injects_answer(source):
    c = criterion()
    bad = finding(uncertainties=[{'detail': 'Feature', 'impact': 'blocking'}])
    client, http, calls = transport([bad, finding(), finding(), answer(c)])
    with http:
        checker = VisualReviewChecker(client, source[3])
        result = checker.check(source[2], CriteriaSet(criteria=[c])).results[0]
    sent = posts(calls)
    assert sent[0]['messages'][1]['images'] == sent[1]['messages'][1]['images']
    assert 'readability' not in json.dumps(sent[0]['format'])
    assert checker.audit[0]['image_reviews'][0]['finding_schema_version'] == 3
    assert len(checker.audit[0]['image_reviews'][0]['validation_failures']) == 1
    assert result.proposed_score == 2


@pytest.mark.parametrize('rgb', [(255, 0, 0), (0, 0, 255)])
@pytest.mark.parametrize('level', [0, 1, 6, 9])
def test_fixture_pixels_survive_different_deflate_encodings(monkeypatch, rgb, level):
    module = _load_control_module()
    original_compress = zlib.compress
    monkeypatch.setattr(module.zlib, 'compress', lambda data: original_compress(data, level))
    assert_solid_fixture(module._solid_png(rgb), rgb)


def test_different_compressed_hashes_do_not_imply_different_pixels(monkeypatch):
    module = _load_control_module()
    original = zlib.compress
    digests = set()
    for level in (0, 1, 6, 9):
        monkeypatch.setattr(module.zlib, 'compress', lambda data, level=level: original(data, level))
        encoded = module._solid_png((255, 0, 0))
        digests.add(hashlib.sha256(encoded).hexdigest())
        assert_solid_fixture(encoded, (255, 0, 0))
    assert len(digests) > 1


def _chunk(kind, payload):
    return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload) & 0xffffffff)


@pytest.mark.parametrize('change', ['one_pixel', 'dimensions', 'filter', 'crc', 'signature', 'truncated', 'trailing_stream'])
def test_fixture_invariant_still_detects_changes_and_corruption(change):
    header = struct.pack('!2I5B', 256, 192, 8, 2, 0, 0, 0)
    raw = bytearray((b'\0' + bytes((255, 0, 0)) * 256) * 192)
    if change == 'one_pixel': raw[4] = 0
    if change == 'dimensions': header = struct.pack('!2I5B', 128, 384, 8, 2, 0, 0, 0)
    if change == 'filter': raw[0] = 1
    packed = zlib.compress(raw)
    if change == 'trailing_stream': packed += b'extra'
    data = b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header) + _chunk(b'IDAT', packed) + _chunk(b'IEND', b'')
    if change == 'crc': data = data[:-1] + bytes([data[-1] ^ 1])
    if change == 'signature': data = b'X' + data[1:]
    if change == 'truncated': data = data[:-3]
    with pytest.raises((AssertionError, zlib.error)):
        assert_solid_fixture(data, (255, 0, 0))


def test_binary_integrity_hashes_remain_hashes_of_original_bytes(source):
    for image in source[2].images:
        assert image.sha256 == hashlib.sha256(image.binary_data).hexdigest()
