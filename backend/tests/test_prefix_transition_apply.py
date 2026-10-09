"""Issue 80: exact-plan command preparation and presentation retirement."""
from datetime import date

from fastapi import HTTPException
import pytest

from app.prefix_transition_contracts import PrefixTransitionApplyRequest
from app.services.prefix_transition_application import validate_approved_plan
from app.services.prefix_transition import plan_transition
from test_prefix_transition import prepared_snapshot


@pytest.mark.parametrize('missing_match', [False, True])
def test_pr102_preparation_selects_first_step_for_each_card_and_root_role(monkeypatch, missing_match):
    from contextlib import contextmanager, nullcontext
    from dataclasses import replace
    import json
    from types import SimpleNamespace
    from app.services import prefix_transition_application as application

    captured = prepared_snapshot()
    plan = plan_transition(captured, ['caro'], {'caro': 2})
    creations = [card for card in plan.cards if card.lifecycle == 'create']
    assert len(creations) == 2
    expected_references = []
    proposed_references = []
    for card in creations:
        card_is_root = json.loads(card.schedule_json)['state'] == 'new'
        reference = next(step for step in plan.proposed_steps if step.card_id == card.card_id
                         and (step.parent_card_id is None) == card_is_root)
        expected_references.append(replace(reference, line_id='first-' + card.card_id))
        proposed_references.extend((
            replace(reference, parent_card_id='opposite-role' if card_is_root else None),
            expected_references[-1], replace(reference, line_id='later-' + card.card_id)))
    if missing_match:
        proposed_references = [step for step in proposed_references
                               if step.card_id != creations[-1].card_id]

    class CountedSteps:
        visits = 0

        def __iter__(self):
            for step in proposed_references:
                self.visits += 1
                yield step

    counted_steps = CountedSteps()
    prepared_plan = plan.model_copy(update={'status': 'no_op', 'proposed_steps': counted_steps})
    @contextmanager
    def connection(**_options):
        yield SimpleNamespace(execute_native=lambda *_args: SimpleNamespace(fetchone=lambda: None))

    calculations = iter(({'whole_repertoire': {'current': {'steps': []}, 'proposed': {'steps': []}}}, prepared_plan))
    monkeypatch.setattr(application.postgres_store, 'connection', connection)
    monkeypatch.setattr(application.structural, 'diagnostic_request', lambda: nullcontext(float('inf')))
    monkeypatch.setattr(application.structural, 'load_snapshot', lambda *_args: captured.source)
    monkeypatch.setattr(application.planner_api, '_calculate', lambda *_args: next(calculations))
    monkeypatch.setattr(application.planner_api, 'discover_memberships', lambda *_args: ())
    monkeypatch.setattr(application.planner_api, 'load_transition_snapshot', lambda *_args, **_kwargs: captured)
    monkeypatch.setattr(application, 'validate_approved_plan', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(application.structural, 'check_available', lambda *_args: None)
    payload = {'operation_id': 'pr102-lookup', 'repertoire_id': plan.repertoire_id,
               'request': approved_request(plan).model_dump(mode='json')}
    if missing_match:
        with pytest.raises(StopIteration):
            application.prepare_application(payload)
    else:
        result = application.prepare_application(payload)
        assert [step.line_id for step in result.creations] == [step.line_id for step in expected_references]
        assert [(step.parent_card_id is None) for step in result.creations] == [
            json.loads(card.schedule_json)['state'] == 'new' for card in creations]
        assert counted_steps.visits == len(proposed_references), 'Replacement preparation rescanned proposed steps'


def approved_request(plan):
    return PrefixTransitionApplyRequest(
        plan_id=plan.plan_id, snapshot_id=plan.snapshot_id,
        transition_snapshot_id=plan.transition_snapshot_id,
        graph_generation=plan.graph_generation, study_day=plan.study_day,
        selected_line_ids=plan.selected_line_ids, candidate_depths={'caro': 2})


def test_issue80_accepts_only_the_exact_approved_shortening_plan():
    plan = plan_transition(prepared_snapshot(), ['caro'], {'caro': 2})
    request = approved_request(plan)
    validate_approved_plan(request, plan, today=plan.study_day)
    for field in ('plan_id', 'snapshot_id', 'transition_snapshot_id'):
        with pytest.raises(HTTPException) as failure:
            validate_approved_plan(request.model_copy(update={field: 'stale'}), plan, today=plan.study_day)
        assert failure.value.status_code == 409
        assert failure.value.detail['code'] == 'stale_plan'


def test_issue80_permanently_deleted_replacement_is_blocked_before_application():
    captured = prepared_snapshot()
    plan = plan_transition(captured, ['caro'], {'caro': 2})
    deleted_target = next(card.card_id for card in plan.cards if card.lifecycle == 'create')
    from test_prefix_transition import changed_snapshot
    blocked = plan_transition(changed_snapshot(captured, 'deleted_cards', [dict(card_id=deleted_target, deleted_at='2026-10-07')]), ['caro'], {'caro': 2})
    assert blocked.status == 'blocked'
    assert any(blocker.code == 'permanently_deleted_target' and blocker.object_id == deleted_target for blocker in blocked.blockers)
    with pytest.raises(HTTPException) as failure:
        validate_approved_plan(approved_request(blocked), blocked, today=blocked.study_day)
    assert failure.value.detail['code'] == 'blocked_plan'


def test_issue80_changed_study_day_and_blocked_plan_never_authorize_mutation():
    plan = plan_transition(prepared_snapshot(), ['caro'], {'caro': 2})
    with pytest.raises(HTTPException) as failure:
        validate_approved_plan(approved_request(plan), plan, today='2099-01-01')
    assert failure.value.detail['code'] == 'stale_plan'
    with pytest.raises(HTTPException) as failure:
        validate_approved_plan(approved_request(plan), plan.model_copy(update={'status': 'blocked'}), today=plan.study_day)
    assert failure.value.detail['code'] == 'blocked_plan'


def test_issue80_http_apply_requires_stable_identity_and_keeps_preparation_outside_api(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, prefix_transition_apply_api as api
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: True)
    sent = []
    from starlette.responses import JSONResponse
    monkeypatch.setattr(api, 'dispatch_command', lambda command, payload, **options: sent.append((command, payload, options)) or JSONResponse(status_code=202,content={'operation_id':'approved-op','state':'pending'},headers={'Location':'/api/operations/approved-op'}))
    client = TestClient(main.app)
    plan = plan_transition(prepared_snapshot(), ['caro'], {'caro': 2})
    payload = approved_request(plan).model_dump(mode='json')
    assert client.post('/api/repertoires/rep/prefix-transition/apply', json=payload).status_code == 422
    response = client.post('/api/repertoires/rep/prefix-transition/apply', json=payload, headers={'Idempotency-Key': 'approved-op'})
    assert response.status_code == 202 and response.headers['Location']=='/api/operations/approved-op'
    assert sent == [
        ('repertoire.prefix_transition.apply', {'operation_id':'approved-op','repertoire_id':'rep','request':payload},
         {'idempotency_key':'approved-op','wait_seconds':0,'background':False})]
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: False)
    response = client.post('/api/repertoires/rep/prefix-transition/apply', json=payload, headers={'Idempotency-Key':'approved-op'})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'unsupported_backend'


def test_issue80_apply_dispatch_does_not_hold_a_foreground_lease_during_preparation(monkeypatch):
    import asyncio
    from app import main, database
    from app.services.activity_gate import activity_gate
    from starlette.requests import Request
    from starlette.responses import Response
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    seen = []
    async def probe():
        async def downstream(_request):
            seen.append((activity_gate.in_background, activity_gate.foreground_waiting, database._query_only_request.get()))
            return Response(status_code=204)
        return await main.prioritize_foreground_requests(Request({'type':'http','method':'POST','path':'/api/repertoires/rep/prefix-transition/apply','headers':[],'query_string':b''}), downstream)
    assert asyncio.run(probe()).status_code == 204
    assert seen == [(True, False, False)]


def test_issue80_checkpoint_locks_original_card_and_rejects_retirement_before_evidence_writes():
    from types import SimpleNamespace
    from app.services.postgres_opening_evidence import _validate_checkpoint_scope
    statements = []
    class Database:
        def execute_native(self, query, parameters):
            statements.append(query)
            assert query.startswith('SELECT')
            return SimpleNamespace(fetchone=lambda: None if 'FROM deleted_cards' in query else {'revision':1,'archived':1})
    request = SimpleNamespace(manifest=SimpleNamespace(card_id='old-card',card_revision=1))
    with pytest.raises(HTTPException) as failure:
        _validate_checkpoint_scope(Database(), request, completing_review=False)
    assert failure.value.detail['code'] == 'card_archived'
    assert failure.value.detail['aggregate_review_allowed'] is False
    assert statements == [
        'SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
        'SELECT 1 FROM deleted_cards WHERE card_id=%s',
        'SELECT revision,archived FROM cards WHERE id=%s FOR UPDATE',
    ]


def test_issue80_external_worker_claims_transition_slices_and_owns_atomic_cursor_completion(monkeypatch):
    from app import tasks
    from app.services import prefix_transition_application as application
    assert application.TASK_KIND in tasks._SUPPORTED_BACKGROUND_KINDS
    task={'kind':application.TASK_KIND,'id':'stage-task','generation':1,'lease_token':'lease','payload':{'operation_id':'approved'}}
    calls=[]
    monkeypatch.setattr(tasks,'defer_paused_defensive_task',lambda _:False)
    monkeypatch.setattr(tasks,'current_delivery',lambda _:True)
    monkeypatch.setattr(application,'execute_application_slice',lambda claimed:calls.append(claimed) or False)
    monkeypatch.setattr(tasks,'complete_task',lambda *_args,**_options:pytest.fail('Worker separately completed a cursor-owned transition slice'))
    tasks.execute_background_slice.run(task)
    assert calls==[task]



def test_issue80_openapi_binds_the_exact_request_and_deferred_completion_contract():
    from app.main import app
    schema=app.openapi()
    route=schema['paths']['/api/repertoires/{identifier}/prefix-transition/apply']['post']
    assert route['requestBody']['content']['application/json']['schema']['$ref'].endswith('/PrefixTransitionApplyRequest')
    assert route['responses']['202']['headers']['Location']['schema']=={'type':'string'}
    assert route['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/PrefixTransitionApplicationResult')
    identity=next(parameter for parameter in route['parameters'] if parameter['name']=='Idempotency-Key')
    assert identity['required'] is True


def test_issue80_retired_study_self_assessment_conflicts_before_grading_and_completed_receipt_replays():
    import json
    from types import SimpleNamespace
    from app.study_attempt_commands import self_assess_study_attempt
    statements=[]
    saved={'card_id':'old','queue_entry_id':1,'context':'review','result_json':None,'retired_operation_id':'approved'}
    class Database:
        def execute(self,query,parameters=()):
            statements.append((query,parameters))
            assert query.startswith('SELECT')
            value = {'study_id':'study'} if 'FROM study_exercises' in query else ('2026-10-08',) if 'SELECT queue_date' in query else saved
            return SimpleNamespace(fetchone=lambda:value)
        execute_native=execute
    payload={'attempt_id':'unfinished','exercise_id':'exercise','study_id':'study','assessment':{'rating':'correct'}}
    with pytest.raises(HTTPException) as failure:
        self_assess_study_attempt(Database(),payload)
    assert failure.value.status_code==409 and 'retired' in failure.value.detail
    assert any('FROM cards' in query and parameters==('old',) for query,parameters in statements)
    saved['result_json']=json.dumps({'rating':'correct','persisted':True})
    assert self_assess_study_attempt(Database(),payload)=={'rating':'correct','persisted':True}


def test_issue80_application_reservation_lock_budget_is_independent_of_snapshot_size():
    """The write boundary must protect absent identities without one advisory lock each."""
    from dataclasses import replace
    from types import SimpleNamespace
    from app.services import prefix_transition_application as application
    statements = []
    class Database:
        def execute_native(self, query, parameters=()):
            statements.append(query)
            return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: (True,) if 'pg_try_advisory' in query else None)
    prepared = application.PreparedApplication(
        payload={'operation_id': 'approved'},
        plan=SimpleNamespace(status='ready', repertoire_id='rep', study_day=date.today().isoformat()),
        snapshot=replace(prepared_snapshot(), lookup_card_ids=tuple(f'absent-{index}' for index in range(1024))),
        repertoire_ids=tuple(f'shared-{index}' for index in range(128)))
    application.lock_and_revalidate(Database(), prepared)
    advisory_calls = [query for query in statements if 'pg_advisory' in query or 'pg_try_advisory' in query]
    assert len(advisory_calls) == 1, f'Application allocated {len(advisory_calls)} reservation locks'
    assert 'pg_try_advisory_xact_lock' in advisory_calls[0]
    assert all('NOWAIT' in query for query in statements if 'FOR UPDATE' in query or 'FOR SHARE' in query)


def test_issue80_completion_handoff_uses_the_actual_completed_queue_generation(monkeypatch):
    from types import SimpleNamespace
    from app.services import prefix_transition_application as application
    captured = []
    class Database:
        def execute_native(self, query, parameters=()):
            return SimpleNamespace(fetchone=lambda: (15,))
    monkeypatch.setattr(application, 'release_fences', lambda *_: None)
    monkeypatch.setattr(application, 'enqueue_completion', lambda *arguments: captured.append(arguments[1:]))
    today = date.today().isoformat()
    current = dict(operation_id='first', repertoire_id='rep', graph_generation=2,
                   integrity_task_id='integrity', integrity_generation=3,
                   queue_task_id='shared-queue', queue_generation=14, queue_date=today)
    application.finish_application(Database(), current, SimpleNamespace(plan_id='approved', repertoire_id='rep'))
    assert captured == [('shared-queue', 15, today)]


def test_issue80_sqlite_task_failure_and_retry_never_call_postgres_transition_hooks(tmp_path, monkeypatch):
    from app import database
    from app.services import durable_tasks, prefix_transition_application
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'compatibility.db')
    monkeypatch.setattr(durable_tasks.postgres_store, 'configured', lambda: False)
    for hook in ('lock_linked_receipts', 'record_linked_failure'):
        monkeypatch.setattr(prefix_transition_application, hook, lambda *_args: pytest.fail('SQLite used a PostgreSQL transition hook'))
    database.initialize()
    from app.services.database_executor import database_writer
    database_writer.start()
    try:
        task = durable_tasks.enqueue_task('opening_graph_rebuild', 'compatibility', {'repertoire_id': 'compatibility'})
        claimed = durable_tasks.claim_task('opening_graph_rebuild')
        assert claimed['id'] == task['id']
        with database.connection() as connection:
            connection.execute('UPDATE background_tasks SET max_attempts=1 WHERE id=?', (task['id'],))
        result = durable_tasks.fail_task(task['id'], claimed['generation'], claimed['lease_token'], RuntimeError('compatibility failure'))
        assert result['state'] == 'failed'
        durable_tasks.retry_task(task['id'])
        replay = durable_tasks.claim_task('opening_graph_rebuild')
        assert replay['id'] == task['id'] and replay['generation'] == claimed['generation']
    finally:
        database_writer.stop()


@pytest.mark.parametrize('scenario', ['eligible', 'not_due', 'unexpected_error', 'expired'])
def test_issue80_http_proof_redelivers_only_eligible_original_foreground_yield(monkeypatch, scenario):
    import importlib.util
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('prefix_application_proof',
        Path(__file__).resolve().parents[2] / 'scripts/check_postgres_prefix_transition_application.py')
    proof = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proof)
    receipt = {'state': 'retrying', 'attempt_count': 1,
        'next_retry_at': (datetime.now(timezone.utc) + timedelta(seconds=60 if scenario == 'not_due' else -1)).isoformat(),
        'last_error': {'class': 'SerializationFailure',
            'message': 'Transition preparation yielded to foreground work; retry its durable operation'}}
    if scenario == 'unexpected_error':
        receipt['last_error']['class'] = 'TransactionTimeout'
    elapsed = [0.0]
    delivered = []
    monkeypatch.setattr(proof.time, 'monotonic', lambda: elapsed[0])
    def advance(_seconds):
        elapsed[0] += 1
        if delivered and elapsed[0] == 2:
            receipt.update(state='pending', transition={'state': 'staging'})
    monkeypatch.setattr(proof.time, 'sleep', advance)
    monkeypatch.setattr(proof, 'read_operation', lambda operation_id: dict(receipt, operation_id=operation_id))
    def redeliver_original():
        delivered.append('original-operation')
    if scenario == 'eligible':
        result = proof.wait_for_staged_http_application('original-operation', redeliver_original, 10)
        assert result['operation_id'] == 'original-operation'
        assert delivered == ['original-operation']
    else:
        with pytest.raises(AssertionError):
            proof.wait_for_staged_http_application('original-operation', redeliver_original,
                                                  0 if scenario == 'expired' else 10)
        assert delivered == []
