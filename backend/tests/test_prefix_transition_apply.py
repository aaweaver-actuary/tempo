"""Issue 80: exact-plan command preparation and presentation retirement."""
from datetime import date

from fastapi import HTTPException
import pytest

from app.prefix_transition_contracts import PrefixTransitionApplyRequest
from app.services.prefix_transition_application import validate_approved_plan
from app.services.prefix_transition import plan_transition
from test_prefix_transition import prepared_snapshot


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
            return SimpleNamespace(fetchone=lambda: {'revision':1,'archived':1})
    request = SimpleNamespace(manifest=SimpleNamespace(card_id='old-card',card_revision=1))
    with pytest.raises(HTTPException) as failure:
        _validate_checkpoint_scope(Database(), request, completing_review=False)
    assert failure.value.detail['code'] == 'card_archived'
    assert failure.value.detail['aggregate_review_allowed'] is False
    assert statements == ['SELECT revision,archived FROM cards WHERE id=%s FOR UPDATE']


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
