"""HTTP and defense decisions retain semantic storage and borrowed lifecycle."""
from contextlib import contextmanager,nullcontext
from datetime import date,datetime,timezone
from types import SimpleNamespace
import json

from fastapi import HTTPException
import pytest

from tempo_learning_runtime import api,defense
from tempo_learning_runtime.config import Configuration
from tempo_learning_runtime.models import DefenseRecognitionRequest
from tempo_learning_runtime.review_conflicts import ReviewConflict

DAY=date(2026,10,21);NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)


@pytest.fixture
def endpoints(monkeypatch):
    monkeypatch.setattr(api,'Database',lambda _: object())
    monkeypatch.setattr(api.gateway,'_handlers',{})
    app=api.create_app(Configuration('workspace','unused','unused','x'*40,maintenance=False))
    return {route.path:route.endpoint for route in app.routes if hasattr(route,'endpoint')}


@pytest.mark.parametrize('conflict',[False,True])
def test_reconciliation_retains_handled_conflict_savepoint_order(monkeypatch,conflict):
    events=[];database=object()
    for name in ['begin_reconciliation','rollback_reconciliation','release_reconciliation']:
        monkeypatch.setattr(api.storage,name,lambda db,_name=name: events.append(_name) if db is database else pytest.fail('Changed transaction'))
    def submit(db,payload):
        assert db is database and payload=={'card':'card'};events.append('review')
        if conflict:raise ReviewConflict('changed','Changed')
        return dict(persisted=True)
    monkeypatch.setattr(api,'submit_legacy_review',submit)
    result=api.reconcile(database,dict(card='card'))
    assert result['persisted'] is (not conflict)
    assert events==['begin_reconciliation','review',*(['rollback_reconciliation'] if conflict else []),'release_reconciliation']


def test_saved_endgame_rechecks_retirement_under_card_lock_before_clock_or_insert(monkeypatch):
    events=[]
    monkeypatch.setattr(api.storage,'active_endgame_template',lambda *_: dict(card_id='card'))
    monkeypatch.setattr(api.storage,'lock_endgame_card',lambda *_: events.append('card lock') or (1,))
    monkeypatch.setattr(api.storage,'endgame_enabled',lambda *_: events.append('template recheck') or (0,))
    monkeypatch.setattr(api.storage,'insert_endgame_attempt',lambda *_: pytest.fail('Retired template reached write'))
    monkeypatch.setattr(api,'datetime',SimpleNamespace(now=lambda *_: pytest.fail('Retired template read clock')))
    with pytest.raises(HTTPException) as error:api.save_endgame_attempt(None,dict(template_id='template'))
    assert error.value.status_code==409 and events==['card lock','template recheck']


@pytest.mark.parametrize('matching',[False,True])
def test_readiness_decision_retains_source_migration_correspondence(monkeypatch,endpoints,matching):
    monkeypatch.setattr(api,'connection',lambda **_: nullcontext(object()))
    monkeypatch.setattr(api.storage,'migration_state',lambda _: ({'one':'hash'},{'one':'hash'} if matching else {'one':'changed'}))
    response=endpoints['/health/ready']()
    if matching:assert response==dict(status='ready',workspace_id='workspace')
    else:assert response.status_code==503 and json.loads(response.body)==dict(
        status='unavailable',domain='schema',reason='migration_history_outdated'
    )


@pytest.mark.parametrize(
    'failure,expected',
    [
        (api.RuntimeRoleScopeError('restricted'),
         dict(status='unavailable',domain='credentials',reason='restricted_role_required')),
        (api.WorkspaceBindingError('workspace'),
         dict(status='unavailable',domain='credentials',reason='workspace_binding_mismatch')),
        (api.psycopg.errors.InsufficientPrivilege('denied'),
         dict(status='unavailable',domain='credentials',reason='insufficient_privileges')),
        (api.psycopg.errors.UndefinedTable('missing'),
         dict(status='unavailable',domain='schema',reason='migration_state_unavailable')),
        (api.psycopg.OperationalError('down'),
         dict(status='unavailable',domain='database',reason='connection_unavailable')),
    ],
)
def test_readiness_classifies_database_schema_and_credential_failures(
    monkeypatch,endpoints,failure,expected
):
    def broken_connection(**_):
        raise failure
    monkeypatch.setattr(api,'connection',broken_connection)
    response=endpoints['/health/ready']()
    assert response.status_code==503 and json.loads(response.body)==expected


@pytest.mark.parametrize('status,head,day,expected',[
    ('queued',4,DAY.isoformat(),'head'),('queued',5,DAY.isoformat(),'queued'),
    ('complete',4,DAY.isoformat(),'completed'),('superseded',None,DAY.isoformat(),'unavailable'),
    ('queued',4,'2026-10-20','unavailable'),
])
def test_entry_state_consumes_semantic_head_and_preserves_mapping(monkeypatch,endpoints,status,head,day,expected):
    monkeypatch.setattr(api,'connection',lambda **_: nullcontext(object()))
    monkeypatch.setattr(api,'date',SimpleNamespace(today=lambda: DAY))
    monkeypatch.setattr(api.storage,'queue_entry_state',lambda *_: dict(status=status,attempt_failed=1,queue_date=day))
    monkeypatch.setattr(api.storage,'available_head',lambda *_: (head,) if head else None)
    monkeypatch.setattr(api.queue_application,'available_attempt_sql',lambda: pytest.fail('HTTP reached SQL builder'))
    assert endpoints['/api/queue/entries/{entry_id}/state'](4)==dict(state=expected,attempt_failed=True)


def test_evidence_missing_attempt_does_not_open_event_read(monkeypatch,endpoints):
    reads=[]
    @contextmanager
    def connection(**options):reads.append(options);yield None
    monkeypatch.setattr(api,'connection',connection)
    monkeypatch.setattr(api.storage,'opening_attempt',lambda *_: None)
    monkeypatch.setattr(api.storage,'opening_events',lambda *_: pytest.fail('Missing attempt read events'))
    with pytest.raises(HTTPException) as error:endpoints['/api/opening-evidence/attempts/{attempt_id}']('attempt')
    assert error.value.status_code==404 and reads==[dict(read_only=True)]


def test_presentation_preserves_allowlist_and_feedback_requires_committed_scope(monkeypatch,endpoints):
    monkeypatch.setattr(api,'connection',lambda **_: nullcontext(object()))
    specification=dict(type='choice',prompt='Choose',hint='Hint',options=[dict(id='a',text='A')],correct_option_ids=['a'])
    monkeypatch.setattr(api.storage,'published_study_exercise',lambda *_: dict(current_revision=2,fen='fen',specification_json=json.dumps(specification)))
    result=endpoints['/api/studies/{study_id}/exercises/{exercise_id}/present']('study','exercise')
    assert result==dict(id='exercise',revision=2,type='choice',prompt='Choose',hint='Hint',fen='fen',options=[dict(id='a',text='A')])
    monkeypatch.setattr(api.storage,'committed_study_specification',lambda *_: None)
    with pytest.raises(HTTPException) as error:endpoints['/api/studies/{study_id}/exercises/{exercise_id}/attempts/{attempt_id}/feedback']('study','exercise','attempt')
    assert error.value.status_code==404


def test_publication_retry_keeps_prepared_v2_dispatch_and_identity(monkeypatch,endpoints):
    monkeypatch.setattr(api,'connection',lambda **_: nullcontext(object()))
    monkeypatch.setattr(api.gateway,'read_operation',lambda *_args,**_kwargs: dict(state='queued'))
    monkeypatch.setattr(api.storage,'operation_intent',lambda *_: ('publication.activate.v2','{"publication_id":"pub"}'))
    calls=[]
    monkeypatch.setattr(api.publication_bridge,'dispatch_activation',lambda *args: calls.append(args) or dict(status='activated'))
    monkeypatch.setattr(api.gateway,'execute',lambda *_args,**_kwargs: pytest.fail('V2 retry bypassed preparation'))
    assert endpoints['/internal/publications/operations/{operation_id}/retry']('activation')==dict(status='activated')
    assert calls==[({'publication_id':'pub'},'activation')]


def candidate():
    return dict(id='candidate',card_id='card',exercise_revision=2,serving_active=1,archived=0,
        approved_at='accepted',validation_state='engine_supported',game_id='game',
        evidence_json=json.dumps(dict(seed=dict(geometry=dict(knight_from='b3',knight_to='c5',king=dict(square='e4'),major=dict(square='d3'))))),
        validation_json='{"refutation_uci":[]}',definition={})


@pytest.fixture
def completion(monkeypatch):
    events=[];current=candidate()
    monkeypatch.setattr(defense.storage,'requires_queue_lock',lambda: True)
    monkeypatch.setattr(defense.storage,'lock_card',lambda *_: events.append('card lock'))
    monkeypatch.setattr(defense.storage,'queue_date',lambda *_: (DAY.isoformat(),))
    monkeypatch.setattr(defense,'lock_queue_date_for_position',lambda *_: events.append('queue lock'))
    monkeypatch.setattr(defense,'_candidate',lambda *_: events.append('current definition') or current)
    monkeypatch.setattr(defense.storage,'prior_attempt',lambda *_: None)
    monkeypatch.setattr(defense.storage,'queued_entry',lambda *_: {'id':4})
    monkeypatch.setattr(defense,'datetime',SimpleNamespace(now=lambda *_: events.append('clock') or NOW))
    monkeypatch.setattr(defense,'date',SimpleNamespace(today=lambda: DAY))
    monkeypatch.setattr(defense,'capture_daily_queue_order',lambda *_: events.append('capture') or 'order')
    monkeypatch.setattr(defense.storage,'complete_queue_entry',lambda *_: events.append('complete'))
    monkeypatch.setattr(defense,'preserve_daily_queue_order',lambda *_args,**_kwargs: events.append('preserve'))
    monkeypatch.setattr(defense.storage,'insert_attempt',lambda *_: events.append('receipt'))
    return current,events


def complete(current):
    return defense._complete_defense_in_transaction(None,current,attempt_id='attempt',exercise_revision=2,
        queue_entry_id=4,move_uci='e2e4',response=dict(status='correct'),light_first_interval_days=2)


def test_defense_completion_missing_queue_stops_before_replay_or_clock(monkeypatch,completion):
    current,events=completion
    monkeypatch.setattr(defense.storage,'queue_date',lambda *_: None)
    monkeypatch.setattr(defense.storage,'prior_attempt',lambda *_: pytest.fail('Missing queue reached replay'))
    with pytest.raises(ValueError,match='no longer the active'):complete(current)
    assert events==['card lock']


def test_defense_completion_replay_returns_before_retired_or_grading_decisions(monkeypatch,completion):
    current,events=completion;current['serving_active']=0
    monkeypatch.setattr(defense.storage,'prior_attempt',lambda *_: dict(candidate_id='candidate',move_uci='e2e4',queue_entry_id=4,grade_json='{"status":"correct"}'))
    assert complete(current)==dict(status='correct',idempotent=True)
    assert events==['card lock','queue lock','current definition']


@pytest.mark.parametrize('after',[None,3,'no repeat'])
def test_defense_completion_retains_scheduling_repeat_and_receipt_order(monkeypatch,completion,after):
    current,events=completion
    def schedule(*_args,**kwargs):
        assert kwargs['reviewed_at']==NOW and kwargs['review_day']==DAY
        events.append('schedule');return dict(requeue_today=after!='no repeat',requeue_after_cards=after)
    monkeypatch.setattr(defense,'apply_scheduling_review',schedule)
    monkeypatch.setattr(defense.storage,'next_cycle',lambda *_: events.append('cycle') or 2)
    monkeypatch.setattr(defense.storage,'next_position',lambda *_: events.append('position') or 9)
    monkeypatch.setattr(defense.storage,'insert_reinforcement',lambda *_: events.append('tail repeat'))
    monkeypatch.setattr(defense,'ensure_card_queued_after',lambda *_args,**_kwargs: events.append('offset repeat'))
    assert complete(current)==dict(status='correct',idempotent=False)
    repeat=['cycle','position','tail repeat'] if after is None else ['offset repeat'] if after==3 else []
    assert events==['card lock','queue lock','current definition','clock','capture','schedule','complete',*repeat,'preserve','receipt']


def request(**changes):
    return DefenseRecognitionRequest.model_validate({**dict(attempt_id='attempt',exercise_revision=2,rubric_version=3,
        queue_entry_id=4,no_concrete_threat=False,dangerous_piece_square='b3',destination_square='c5',
        king_square='e4',major_square='d3',consequence='checking_fork'),**changes})


def test_recognition_rubric_rejection_precedes_any_database_scope(monkeypatch):
    monkeypatch.setattr(defense,'read_connection',lambda: pytest.fail('Changed rubric opened database'))
    with pytest.raises(ValueError,match='rubric changed'):defense.submit_defense_recognition('candidate',request(rubric_version=2))


def test_recognition_rechecks_evidence_before_writing_answer(monkeypatch):
    first=candidate();changed={**first,'validation_state':'validated_control'};facts=iter([first,changed]);events=[]
    monkeypatch.setattr(defense,'_candidate',lambda *_: next(facts))
    monkeypatch.setattr(defense,'_exercise_from_rows',lambda *_: SimpleNamespace(anchor=None,seed=None,evaluated_reports=[SimpleNamespace(lines=[None])]))
    monkeypatch.setattr(defense,'recognition_preview',lambda *_: object())
    monkeypatch.setattr(defense.storage,'prior_recognition',lambda *_: None)
    monkeypatch.setattr(defense.storage,'queued_membership',lambda *_: (1,))
    monkeypatch.setattr(defense.storage,'source_game',lambda *_: None)
    monkeypatch.setattr(defense.storage,'requires_queue_lock',lambda: True)
    monkeypatch.setattr(defense.storage,'lock_card',lambda *_: events.append('card lock'))
    monkeypatch.setattr(defense.storage,'queue_date',lambda *_: (DAY.isoformat(),))
    monkeypatch.setattr(defense,'lock_queue_date_for_position',lambda *_: events.append('queue lock'))
    monkeypatch.setattr(defense.storage,'insert_recognition',lambda *_: pytest.fail('Changed evidence wrote answer'))
    with pytest.raises(ValueError,match='evidence changed'):defense.submit_defense_recognition('candidate',request(),write_database=object())
    assert events==['card lock','queue lock']
