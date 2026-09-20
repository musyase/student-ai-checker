"""Pure rubric-rendering tests: these do not infer with an LLM or grade a student.

Load the stdlib-only compiler directly so tests of the prompt compiler do not
implicitly depend on importing the CLI, an Ollama server, or PDF/DOCX rendering.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import inspect
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
PATH = ROOT / 'src/student_ai_checker/grading/evaluation_context.py'
SPEC = importlib.util.spec_from_file_location('rubric_context_under_test', PATH)
context = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(context)
compile_checks = context.compile_evaluation_checks
ContextError = context.EvaluationContextError


@pytest.fixture
def rubric():
    data = json.loads((ROOT/'examples/pr1.criteria.approved.json').read_text(encoding='utf-8'))
    return {c['id']: c for c in data['criteria']}


def parts(criterion_id):
    return [(key, text, 1) for key, text in context._FRAGMENTS[criterion_id].items()]


@pytest.mark.parametrize('component', ['button', 'input', 'font'])
def test_problems_are_context_not_the_achievement_to_reward(rubric, component):
    cid = 'pr1.usability'; original = copy.deepcopy(rubric[cid])
    checks = {c['check_id']: c for c in compile_checks(cid, original['description'], parts(cid))}
    c = checks[component]
    assert c['operation'] == 'evaluate_proposed_fix'
    assert c['requirement'].startswith('Предложено осмысленное исправление проблемы:')
    assert c['requirement'] != c['source_fragment']
    assert 'Сохранение или усиление' in c['interpretation']
    assert c['source_fragment'] in c['requirement']
    assert all(s in original['description'] for s in c['rubric_fragments'])
    assert rubric[cid] == original


@pytest.mark.parametrize('component,operation', [
    ('initial','report_initial_state'), ('actions','report_own_actions'),
    ('outcome','report_reached_stage'),
])
def test_temporal_roles_stay_separate(rubric, component, operation):
    cid = 'pr1.installation-report'
    c = {v['check_id']:v for v in compile_checks(cid,rubric[cid]['description'],parts(cid))}[component]
    assert c['operation'] == operation
    assert c['source_fragment'] in rubric[cid]['description']
    assert 'Не выводи результат действий из одного факта исходного состояния' in c['interpretation']
    assert ('conditional_requirement' in c) == (component == 'outcome')


def test_conditional_problem_does_not_become_mandatory(rubric):
    cid = 'pr1.installation-report'
    outcome = compile_checks(cid,rubric[cid]['description'],parts(cid))[-1]
    conditional = outcome['conditional_requirement']
    assert conditional['when'] == 'при возникновении затруднения'
    assert conditional['then'] == 'проблема и действия после неё'
    assert conditional['when'] in rubric[cid]['description']
    assert conditional['then'] in rubric[cid]['description']
    assert 'не требуй обязательной фразы об отсутствии проблем' in conditional['interpretation']
    assert 'Конкретное противоречие' in conditional['interpretation']
    assert 'Незавершённая установка сама по себе не снижает балл.' in outcome['rubric_fragments']


@pytest.mark.parametrize('cid', list(context._FRAGMENTS))
@pytest.mark.parametrize('change', ['append','space','polarity'])
def test_changed_parent_rubric_fails_closed(rubric, cid, change):
    description=rubric[cid]['description']
    if change=='append': description+=' Другое условие.'
    elif change=='space': description+=' '
    else: description=description.replace('не ', 'обязательно ', 1)
    with pytest.raises(ContextError, match='parent_rubric_context_changed'):
        compile_checks(cid,description,parts(cid))


@pytest.mark.parametrize('cid', list(context._FRAGMENTS))
@pytest.mark.parametrize('change', ['requirement','points','bool','id','missing','duplicate'])
def test_mismatched_fixed_profile_is_not_reinterpreted(rubric, cid, change):
    p=parts(cid)
    if change=='requirement':p[0]=(p[0][0],'другой смысл',1)
    elif change=='points':p[0]=(p[0][0],p[0][1],2)
    elif change=='bool':p[0]=(p[0][0],p[0][1],True)
    elif change=='id':p[0]=('another',p[0][1],1)
    elif change=='missing':p.pop()
    else:p.append(p[0])
    with pytest.raises(ContextError):compile_checks(cid,rubric[cid]['description'],p)


@pytest.mark.parametrize('cid', list(context._FRAGMENTS))
@pytest.mark.parametrize('order',list(itertools.permutations(range(3))))
def test_reordering_components_does_not_change_their_meaning(rubric, cid, order):
    base=parts(cid); ordered=[base[i] for i in order]
    result=compile_checks(cid,rubric[cid]['description'],ordered)
    expected=compile_checks(cid,rubric[cid]['description'],base)
    assert [c['check_id'] for c in result]==[p[0] for p in ordered]
    assert {c['check_id']:c for c in result}=={c['check_id']:c for c in expected}
    assert base==parts(cid)


def test_unknown_criterion_keeps_original_requirement_without_pr1_rules():
    p=[('item','Собственное требование другого задания',2)]
    assert compile_checks('other','Другой текст',p)==[{
        'check_id':'item','requirement':p[0][1]}]


def test_results_are_fresh_copies(rubric):
    cid='pr1.usability';description=rubric[cid]['description']
    first=compile_checks(cid,description,parts(cid));second=copy.deepcopy(first)
    first[0]['rubric_fragments'].append('tampered')
    assert compile_checks(cid,description,parts(cid))==second


def test_compiler_cannot_read_submission_or_select_an_expected_score():
    assert set(inspect.signature(compile_checks).parameters)=={'criterion_id','description','parts'}
    text=PATH.read_text(encoding='utf-8')
    for literal in ('expected_score','Подготовиться к паре','student_name','S000001','qwen3'):
        assert literal not in text


def _assignments_from_controls():
    path=ROOT/'tests/integration/test_checked_text_real.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    return {node.targets[0].id:ast.literal_eval(node.value)
            for node in tree.body if isinstance(node,ast.Assign)
            and isinstance(node.targets[0],ast.Name)
            and node.targets[0].id in {'CASES','VARIANT_CASES','EXPECTED_COMPONENTS'}}


def test_original_eight_fixtures_and_expectations_are_unchanged():
    cases=_assignments_from_controls()['CASES']
    digest=hashlib.sha256(json.dumps(cases,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    assert digest=='1e4797db5be12c912d411fd58a8f5c376e61a4683ded5603f1d94525ba0e10a9'
    assert len(cases)==8


def test_contrastive_cases_require_correct_components_not_just_sum():
    data=_assignments_from_controls()
    assert len(data['VARIANT_CASES'])==8
    all_cases=data['CASES']+data['VARIANT_CASES']
    assert len({c[0] for c in all_cases})==16
    assert set(data['EXPECTED_COMPONENTS'])=={c[0] for c in all_cases}
    for case_id,_,_,expected in all_cases:
        states=data['EXPECTED_COMPONENTS'][case_id]
        assert len(states)==3 and sum(v=='met' for v in states)==expected


def _assertion_helper():
    path=ROOT/'tests/integration/test_checked_text_real.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='assert_component_results')
    ns={'json':json,'PARTS':{'test':[SimpleNamespace(id=x) for x in ('a','b','c')]}}
    exec(compile(ast.Module(body=[func],type_ignores=[]),str(path),'exec'),ns)
    return ns['assert_component_results']


def test_compensating_model_errors_cannot_pass_the_new_assertions():
    record={'result':{'criterion_id':'test'},'audit':{'components':[
        {'check_id':x,'verdict':v,'points':int(v=='met')}
        for x,v in zip(('a','b','c'),('met','met','not_met'))]}}
    # Same total (2) as expected, but wrong individual requirements.
    with pytest.raises(AssertionError):_assertion_helper()(record,('met','not_met','met'))


def test_correct_component_assertions_pass():
    states=('not_met','not_met','met')
    record={'result':{'criterion_id':'test'},'audit':{'components':[
        {'check_id':x,'verdict':v,'points':int(v=='met')}
        for x,v in zip(('a','b','c'),states)]}}
    _assertion_helper()(record,states)
    assert record['expected_components']==list(states)


def _isolated_prompt_path():
    """Run the actual orchestration function until a mocked transport error.

    Extraction and error-result construction are isolated test doubles. This tests
    that compiled checks actually enter the request/audit, NOT score calculation,
    full CLI import, real HTTP, or the interpretation of any model response.
    """
    path=ROOT/'src/student_ai_checker/grading/referenced_text.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    names={'check_text_references','_digest'}
    funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    system=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='REFERENCE_SYSTEM')
    class GradingError(Exception):pass
    class TransportError(Exception):pass
    ns={'json':json,'hashlib':hashlib,'Any':object,
        'REFERENCE_SYSTEM':system,'REFERENCE_CONTRACT_VERSION':1,'SEMANTIC_GUARD_VERSION':1,'REFERENCE_PROMPT_VERSION':6,
        'EVALUATION_CONTEXT_VERSION':4,'EvaluationContextError':ContextError,
        'compile_evaluation_checks':compile_checks,'build_references':lambda doc:(),
        'references_schema':lambda parts,refs:{'fixture_schema':True},
        'GradingValidationError':GradingError,'OllamaError':TransportError,
        '_result':lambda *args,**kwargs:{'isolated_error_result':True}}
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*funcs],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),ns)
    class ModelDouble:
        def __init__(self):self.calls=[]
        def chat_structured(self,**kwargs):
            self.calls.append(kwargs)
            raise TransportError('Mocked transport failure')
    return ns['check_text_references'],ModelDouble


@pytest.mark.parametrize('cid',list(context._FRAGMENTS))
def test_compiled_requirements_are_actually_sent_and_audited(rubric,cid):
    run,Model=_isolated_prompt_path();model=Model()
    c=SimpleNamespace(id=cid,description=rubric[cid]['description'],max_score=3,
                      model_dump_json=lambda:json.dumps(rubric[cid],ensure_ascii=False))
    p=tuple(SimpleNamespace(id=k,requirement=text,points=pts) for k,text,pts in parts(cid))
    _,audit=run(model,c,p,SimpleNamespace(source_sha256='a'*64))
    assert len(model.calls)==1
    checks=json.loads(model.calls[0]['system_prompt'].split('\nFIXED_CHECKS:\n')[1])
    assert checks==compile_checks(cid,c.description,parts(cid))==audit['evaluation_checks']
    assert audit['evaluation_context_version']==4 and audit['reference_prompt_version']==6
    expected=hashlib.sha256(json.dumps(checks,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert audit['evaluation_checks_sha256']==expected
    assert audit['attempts']==[] # A transport failure is still not a model answer.


def test_invalid_context_stops_orchestration_before_model_call(rubric):
    run,Model=_isolated_prompt_path();model=Model();cid='pr1.usability'
    c=SimpleNamespace(id=cid,description=rubric[cid]['description']+' changed',max_score=3)
    p=tuple(SimpleNamespace(id=k,requirement=text,points=pts) for k,text,pts in parts(cid))
    _,audit=run(model,c,p,SimpleNamespace(source_sha256='a'*64))
    assert model.calls==[] and audit['error_code']=='parent_rubric_context_changed'
