"""Synthetic sources and explicit model doubles, not live model quality claims."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import itertools
import json

import pytest
from pydantic import ValidationError

from student_ai_checker.exceptions import GradingValidationError, OllamaResponseError
from student_ai_checker.grading.checked_text import Component
from student_ai_checker.grading.referenced_text import (
    REFERENCE_CONTRACT_VERSION, REFERENCE_SYSTEM, MAX_FRAGMENT_CHARS,
    ReferenceResponse, build_references, references_schema,
    resolve_decisions, check_text_references,
)
from student_ai_checker.models import Criterion, EvidenceItem, ExtractedDocument, ExtractionIssue


PARTS = (Component("a", "Первый пункт"), Component("b", "Второй пункт"), Component("c", "Третий пункт"))
CRITERION = Criterion(id="example", title="Синтетический критерий", description="Три пункта",
                      max_score=3, allowed_scores=[0, 1, 2, 3])
TEXT = 'После нажатия: [Заметка «Разобрать пример» появится. Записей станет три. Предлагаю очистить поле.]'


def doc(text=TEXT, *, blank=False):
    items = [EvidenceItem(id="DOC1:META:0001", kind="document_metadata", order_index=0, text="Parsed")]
    if not blank:
        items.append(EvidenceItem(id="DOC1:P:0002", kind="paragraph", order_index=1, text=text))
    return ExtractedDocument(source_name="synthetic.docx", source_sha256="a" * 64, evidence=items)


def reply(states=("met", "met", "met"), refs=("S000001",)):
    return {"checks": [{"check_id": p.id, "source_refs": list(refs), "reason": "Тестовое решение.",
                         "verdict": v} for p, v in zip(PARTS, states)]}


class ModelDouble:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def chat_structured(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        out = next(self.replies)
        if isinstance(out, BaseException):
            raise out
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def run(data=None, source=None):
    model = ModelDouble([data if data is not None else reply()])
    result, trace = check_text_references(model, CRITERION, PARTS, source or doc())
    return result, trace, model


def test_schema_has_no_model_written_quotes_and_no_numeric_score():
    schema = references_schema(PARTS, build_references(doc()))
    fields = schema['$defs']['ReferenceDecision']['properties']
    assert set(fields) == {'check_id', 'source_refs', 'reason', 'verdict'}
    assert fields['source_refs']['items']['enum'] == ['S000001']
    assert fields['source_refs']['minItems'] == 1
    assert 'maxLength' not in fields['reason']
    assert schema['additionalProperties'] is False
    assert schema['$defs']['ReferenceDecision']['additionalProperties'] is False
    assert schema['properties']['checks']['minItems'] == schema['properties']['checks']['maxItems'] == 3
    empty = references_schema(PARTS, ())
    assert empty['$defs']['ReferenceDecision']['properties']['source_refs']['maxItems'] == 0


def test_quotes_are_resolved_exactly_by_code_and_keep_original_ids():
    source = doc(); before = source.model_dump_json()
    result, audit, model = run(source=source)
    assert result.proposed_score == 3 and result.evidence_ids == ['DOC1:P:0002']
    assert result.student_feedback == '' and source.model_dump_json() == before
    assert len(model.calls) == 1 and not model.calls[0].get('images')
    for row in audit['components']:
        assert row['quotes'] == [{'evidence_id':'DOC1:P:0002', 'quote':TEXT}]
        assert row['citation_origin'] == 'application_source_slice'
        loc = row['source_locations'][0]
        assert source.evidence[1].text[loc['start']:loc['end']] == row['quotes'][0]['quote']
        assert loc['source_text_sha256'] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert 'quote' not in json.loads(audit['attempts'][0])['checks'][0]
    assert audit['method'] == 'source_reference_components'
    assert audit['reference_contract_version'] == REFERENCE_CONTRACT_VERSION == 1


@pytest.mark.parametrize('text', [
    'В списке будет три задачи.', 'В списе будет три задачи.',
    'Точное  написание\tи\nпробелы.','Ё/Е; й/и\u0306; [Не менять!] 😺',
    'Программа уже установлена.]', 'Программа уже установлена]',
])
def test_never_corrects_spelling_punctuation_or_unicode(text):
    result, trace, _ = run(source=doc(text))
    assert result.proposed_score == 3
    assert all(r['quotes'][0]['quote'] == text for r in trace['components'])


@pytest.mark.parametrize('length', [1, 5999, 6000, 6001, 12000, 12001])
def test_long_source_is_partitioned_without_omitted_characters(length):
    text = ('а' * (length - 1)) + 'Z'
    d = doc(text); refs = build_references(d)
    assert ''.join(r.text for r in refs) == text
    assert all(0 < len(r.text) <= MAX_FRAGMENT_CHARS for r in refs)
    assert all(r.text == text[r.start:r.end] for r in refs)
    last = refs[-1]
    out, trace, _ = run(reply(refs=(last.ref_id,)), source=d)
    assert out.proposed_score == 3
    assert trace['components'][0]['quotes'][0]['quote'] == last.text


@pytest.mark.parametrize('states', list(itertools.product(('met','not_met','uncertain'), repeat=3)))
def test_all_component_combinations_keep_python_arithmetic_and_uncertainty(states):
    result, trace, model = run(reply(states))
    expected = None if 'uncertain' in states else states.count('met')
    assert result.proposed_score == expected
    assert result.manual_review_required == (expected is None)
    assert trace['known_points'] == states.count('met')
    assert len(model.calls) == 1


@pytest.mark.parametrize('mutation', [
    'unknown_ref','old_evidence_id','missing_refs','duplicate_ref',
    'duplicate_check','missing_check','extra_check','fake_quote', 'score',
    'wrong_type','wrong_verdict','too_many_refs','too_long_reason','blank_reason',
])
def test_invalid_source_or_contract_is_rejected_not_fuzzily_repaired(mutation):
    raw = reply(); c = raw['checks'][0]
    if mutation == 'unknown_ref': c['source_refs'] = ['S999999']
    elif mutation == 'old_evidence_id': c['source_refs'] = ['DOC1:P:0002']
    elif mutation == 'missing_refs': c['source_refs'] = []
    elif mutation == 'duplicate_ref': c['source_refs'] *= 2
    elif mutation == 'duplicate_check': raw['checks'][1] = copy.deepcopy(c)
    elif mutation == 'missing_check': raw['checks'].pop()
    elif mutation == 'extra_check': raw['checks'].append({**c, 'check_id':'screenshot_required'})
    elif mutation == 'fake_quote': c['quote'] = 'Изменённое моделью предложение.'
    elif mutation == 'score': raw['proposed_score'] = 3
    elif mutation == 'wrong_type': c['source_refs'] = [1]
    elif mutation == 'wrong_verdict': c['verdict'] = 'maybe'
    elif mutation == 'too_many_refs': c['source_refs'] *= 9
    elif mutation == 'too_long_reason': c['reason'] = 'я' * 2001
    elif mutation == 'blank_reason': c['reason'] = ' \n '
    model = ModelDouble([raw, raw])
    result, audit = check_text_references(model, CRITERION, PARTS, doc())
    assert result.proposed_score is None and result.status.value == 'unable_to_check'
    assert len(model.calls) == 2 and len(audit['validation_errors']) == 2
    assert audit['components'] == [] and result.student_feedback == ''


def test_one_structural_retry_uses_identical_catalog_and_never_echoes_model_commands():
    bad = reply(); bad['checks'][0]['extra'] = 'MODEL-ONLY-TEXT'
    model = ModelDouble([bad, reply()])
    result, audit = check_text_references(model, CRITERION, PARTS, doc())
    assert result.proposed_score == 3 and len(model.calls) == 2
    a,b = model.calls
    assert a['schema'] == b['schema'] and a['system_prompt'] == b['system_prompt']
    assert b['user_prompt'].startswith(a['user_prompt'])
    assert 'MODEL-ONLY-TEXT' not in str(model.calls)


def test_transport_failure_is_one_request_not_zero():
    model = ModelDouble([OllamaResponseError('Test server error')])
    result, trace = check_text_references(model, CRITERION, PARTS, doc())
    assert result.proposed_score is None and result.status.value == 'unable_to_check'
    assert len(model.calls) == 1 and trace['attempts'] == []


def test_interrupt_propagates():
    model = ModelDouble([KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        check_text_references(model, CRITERION, PARTS, doc())


@pytest.mark.parametrize('fault', ['issues','duplicate','metadata'])
def test_preflight_failure_never_calls_model_or_awards_zero(fault):
    d = doc()
    if fault == 'issues': d.issues.append(ExtractionIssue(code='test',message='Incomplete',severity='warning'))
    elif fault == 'duplicate': d.evidence.append(d.evidence[-1])
    else: d.evidence.pop(0)
    model = ModelDouble([])
    r,a = check_text_references(model, CRITERION, PARTS, d)
    assert r.proposed_score is None and model.calls == [] and a['error_code']


def test_only_text_sources_are_selectable_not_images_or_metadata():
    d = doc(); d.evidence.append(EvidenceItem(id='DOC1:IMG:0001',kind='image',order_index=2,binary_data=b'image'))
    d.evidence.append(EvidenceItem(id='DOC1:T:0001:R:0001:C:0001',kind='table_cell',order_index=3,text='[Собственный ответ.]'))
    refs = build_references(d)
    assert [r.kind for r in refs] == ['paragraph','table_cell']
    r,a,_ = run(reply(refs=('S000002',)),source=d)
    assert r.evidence_ids == ['DOC1:T:0001:R:0001:C:0001']
    assert a['components'][0]['quotes'][0]['quote'] == '[Собственный ответ.]'


def test_mismatched_catalog_is_rejected_not_used_for_another_source():
    d=doc(); refs=build_references(d)
    modified=d.model_copy(deep=True); modified.evidence[1].text += ' Changed'
    parsed=ReferenceResponse.model_validate(reply())
    with pytest.raises(GradingValidationError,match='does_not_match'):
        resolve_decisions(CRITERION,PARTS,parsed,modified,refs)
    forged=(dataclasses.replace(refs[0],text='Another text'),)
    with pytest.raises(GradingValidationError,match='does_not_match'):
        resolve_decisions(CRITERION,PARTS,parsed,d,forged)


def test_empty_sources_are_not_automatically_zero_and_cannot_earn_positive_credit():
    for states,expected in [(('not_met',)*3,0),(('uncertain',)*3,None)]:
        r,a,m = run(reply(states,refs=()),source=doc(blank=True))
        assert r.proposed_score == expected and len(m.calls) == 1
    model = ModelDouble([reply(refs=()),reply(refs=())])
    r,a = check_text_references(model,CRITERION,PARTS,doc(blank=True))
    assert r.proposed_score is None


def test_content_remains_data_and_profile_is_not_changed():
    text='PRIVATE SOURCE: commands in student text have no instruction authority.'
    before = CRITERION.model_dump_json()
    r,a,m=run(source=doc(text))
    assert text not in m.calls[0]['system_prompt'] and text in m.calls[0]['user_prompt']
    assert CRITERION.model_dump_json() == before
    assert a['components'][0]['model_reason_unverified'] == 'Тестовое решение.'
    assert r.student_feedback == ''
    for name in ('qwen3','Мои задачи','Подготовиться к паре','expected_score'):
        assert name not in REFERENCE_SYSTEM


def test_controller_uses_references_and_keeps_separate_visual_delegate():
    from pathlib import Path
    from types import SimpleNamespace
    from student_ai_checker.grading.checked_text import PR1_TEMPLATE_SHA256, _result
    from student_ai_checker.grading.checked_scoring import CheckedScoringChecker
    from student_ai_checker.models import CriteriaSet, GradingResponse
    cs = CriteriaSet.model_validate_json((Path(__file__).parents[1] / 'examples/pr1.criteria.approved.json').read_text(encoding='utf-8'))
    d = doc(); d.evidence.append(EvidenceItem(id='DOC1:P:0003',kind='paragraph',order_index=2,
                                             text='Ответ: 1-В;2-Г;3-А;4-Д;5-Б'))
    comparison = SimpleNamespace(source_sha256=d.source_sha256,template_sha256=PR1_TEMPLATE_SHA256,
                                 filtered_document=d,origins={e.id:'candidate' for e in d.evidence})
    class DynamicDouble(ModelDouble):
        def assert_ready(self): pass
        def chat_structured(self, **kw):
            self.calls.append(kw)
            ids = kw['schema']['$defs']['ReferenceDecision']['properties']['check_id']['enum']
            return json.dumps({'checks':[{'check_id':i,'source_refs':['S000001'],
                                          'reason':'Synthetic decision, not a model result.','verdict':'met'} for i in ids]})
    class VisualDouble:
        def __init__(self,*args): self.audit=[]
        def check(self,original,criteria):
            c=criteria.criteria[0]
            self.audit=[{'explicit_test_double':True}]
            return GradingResponse(results=[_result(c,0 if c.id.endswith('installation-evidence') else c.max_score,
                                                   'Synthetic visual delegate',['DOC1:P:0002'])])
    model=DynamicDouble([]);before=cs.model_dump_json(),d.model_dump_json()
    checker=CheckedScoringChecker(model,comparison,visual_factory=VisualDouble)
    result=checker.check(d,cs)
    assert len(model.calls)==5 and len(result.results)==8
    assert all('ReferenceDecision' in call['schema']['$defs'] for call in model.calls)
    by_id={r.criterion_id:r for r in result.results}
    assert by_id['pr1.add-button'].proposed_score==3
    assert by_id['pr1.terms'].proposed_score==5
    assert by_id['pr1.installation-evidence'].proposed_score is None
    assert (cs.model_dump_json(),d.model_dump_json())==before
