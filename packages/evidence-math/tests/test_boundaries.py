"""Installed public API and meaningful negative boundary probes."""
from dataclasses import replace
from datetime import timedelta
import json
import subprocess
import sys
import pytest
from tempo_evidence_math import *
from .support import fixture, memory, AS_OF


def test_arithmetic_import_retains_standard_library_only_closure():
    source='''import sys
from tempo_evidence_math import ProbabilityEvidence, route_probability
assert route_probability(ProbabilityEvidence.exact(1),[ProbabilityEvidence.exact(.5)]).lower == .5
assert not {'chess','pydantic','pydantic_core','fsrs','sqlite3'} & set(sys.modules)
'''
    result=subprocess.run([sys.executable,'-I','-c',source],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


def test_public_exports_and_lazy_imports_need_no_forbidden_runtime():
    import tempo_evidence_math as package
    for name in package.__all__:
        assert getattr(package,name) is not None
    assert not {'sqlite3','fsrs','app','redis','psycopg','tempo_scheduling'} & set(sys.modules)


@pytest.mark.parametrize('value',[True,'0.2',float('nan'),float('inf'),10**400,2**53])
def test_oversized_numbers_fail_without_overflow(value):
    with pytest.raises(ValueError):ProbabilityEvidence.exact(value)
    with pytest.raises(ValueError):ScoreBundle('a','1','axis','cohort','assumption','source',{'card':{'score':value}})


def test_all_nested_inputs_are_detached():
    original={'nested':{'ordered':[2,1]}}
    frozen=CanonicalObject(original)
    original['nested']['ordered'].clear()
    assert frozen['nested']['ordered']==(2,1)
    with pytest.raises(TypeError):frozen['nested']['x']=1
    with pytest.raises(TypeError):frozen.json_text='{}'
    detached=frozen.to_dict();detached['nested']['ordered'].clear()
    assert frozen['nested']['ordered']==(2,1)


def test_canonical_object_rejects_storage_deletion():
    frozen = CanonicalObject({'nested': {'ordered': [2, 1]}})
    before, original_hash = frozen.to_dict(), hash(frozen)
    with pytest.raises(TypeError, match='immutable'):
        del frozen.json_text
    with pytest.raises(TypeError, match='immutable'):
        del frozen['nested'].json_text
    assert frozen.to_dict() == before
    assert hash(frozen) == original_hash


def test_projection_unavailable_differs_from_observed_failure_zero():
    binding=fixture().readiness[0];current=binding.snapshot
    assert decision_readiness(current,AS_OF,decision_index=0).readiness_score==0
    assert decision_readiness(replace(current,reviews=()),AS_OF,decision_index=0).readiness_score is None


def test_projection_fixed_time_changes_only_when_explicit_projection_changes():
    binding=fixture().readiness[0];current=replace(binding.snapshot,reviews=(StudyReviewEvidence(1,binding.snapshot.presentation.card_id,AS_OF,'correct',card_revision=1),))
    projected=replace(current,memory=memory(current.presentation,AS_OF,value=.8))
    assert decision_readiness(projected,AS_OF,decision_index=0).readiness_score==.8
    later=AS_OF+timedelta(days=7)
    projected=replace(current,memory=memory(current.presentation,later,value=.4,reference=AS_OF))
    assert decision_readiness(projected,later,decision_index=0).readiness_score==.4
    with pytest.raises(ValueError):replace(projected.memory,version=True)


def test_probability_source_keeps_ordered_context_and_freshness():
    value=CanonicalObject({'mixture':[{'speed':'rapid'},{'speed':'blitz'}]})
    assert value.to_dict()['mixture'][0]['speed']=='rapid'
    assert value != {'mixture':[{'speed':'blitz'},{'speed':'rapid'}]}


def test_study_weight_is_not_a_supported_reach_parameter():
    with pytest.raises(TypeError):route_probability(ProbabilityEvidence.exact(1),(),study_weight=2)


@pytest.mark.parametrize('fen',[
 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 17 42',
 '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
 '4k3/8/8/3p4/8/8/8/4K3 w - d6 0 1'])
def test_pure_position_helper_preserves_legacy_legal_en_passant_semantics(fen):
    import chess
    assert opening_position_key(fen)==' '.join(chess.Board(fen).fen().split()[:4])


def test_policy_reach_cycle_is_bounded_by_supplied_horizon():
    from .support import policy_for, CONTEXT
    moves=('g1f3','g8f6','f3g1','f6g8','g1f3')
    policy=policy_for('loop',(moves,))
    route=PolicyReachRoute('loop','line',moves,'publication')
    graph=FinitePolicyGraphSnapshot(policy,CONTEXT,(PublicationRevision('publication','1','3'*64),),(route,),5)
    result=propagate_policy_reach(policy,graph.routes,(),context=CONTEXT)
    assert len(result.paths)==6
    assert max(len(path.route.move_prefix) for path in result.paths)==5
    assert len(result.positions)<len(result.paths)


def test_runtime_guard_rejects_deliberate_io_process_and_service_initialization():
    import os
    import types
    assert os.environ.get('TEMPO_RUNTIME_GUARD')=='1','Run through the owning qualifier'
    for operation in ("open('/etc/hosts')", "__import__('sqlite3')", "__import__('socket').create_connection(('127.0.0.1',1))", "__import__('subprocess').run([__import__('sys').executable,'-c','pass'])"):
        code=compile('def probe():\n    '+operation,'<deliberate-boundary-probe>','exec')
        namespace={'__name__':'tempo_evidence_math.deliberate_probe'}
        exec(code,namespace)
        with pytest.raises(AssertionError):namespace['probe']()


def test_static_boundary_rejects_transitive_lazy_database_and_clock(tmp_path):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('local_qualifier',Path(__file__).parents[1]/'qualify.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'src/fixture').mkdir(parents=True)
    (tmp_path/'pyproject.toml').write_text('[project]\nname="fixture"\n')
    for source in ('def lazy():\n import sqlite3\n','from datetime import datetime\ndef clock():\n return datetime.now()\n'):
        (tmp_path/'src/fixture/__init__.py').write_text(source)
        with pytest.raises(ValueError,match='Forbidden'):module.static_boundary(tmp_path)


def test_result_and_input_dataclasses_deeply_detach_supplied_lists():
    contributions=[];diagnostics=['reason']
    result=PreparednessResult(0,1,contributions,diagnostics,policy_id='policy')
    contributions.append('mutation');diagnostics.clear()
    assert result.contributions==() and result.diagnostics==('reason',)
    assistance=['hint']
    presentation=PresentationIdentity('card',1,'rep','white')
    event=DecisionObservationEvidence('attempt',presentation,0,'decision','e2e4',AS_OF,assistance_before_response=assistance)
    assistance.clear()
    assert event.assistance_before_response==('hint',)
