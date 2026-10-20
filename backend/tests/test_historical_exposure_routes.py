"""Real HTTP validation/auth with a fake command capability; no broker or DB."""
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import pytest
from app import historical_exposure_routes as routes
from test_historical_exposure_source import fixture


def client(monkeypatch,tmp_path,mode=0o600):
    token=tmp_path/'operator';token.write_text('x'*40);token.chmod(mode)
    monkeypatch.setenv('TEMPO_EXPOSURE_OPERATOR_TOKEN_FILE',str(token))
    monkeypatch.setattr(routes.postgres_store,'configured',lambda:True)
    calls=[]
    def dispatch(*args,**kwargs):calls.append((args,kwargs));return {'state':'accepted_for_verification'}
    monkeypatch.setattr(routes,'dispatch_command',dispatch)
    app=FastAPI();app.include_router(routes.router)
    return TestClient(app),calls


def payload():return fixture()[0].model_dump(mode='json')


def test_operator_authenticated_explicit_one_line_dispatch_contains_no_credential(monkeypatch,tmp_path):
    session,calls=client(monkeypatch,tmp_path)
    response=session.post('/api/learning/historical-exposure',json=payload(),
        headers={'Authorization':'Bearer '+'x'*40,'Idempotency-Key':'explicit-one-line'})
    assert response.status_code==200 and len(calls)==1
    assert calls[0][0][0]=='historical_exposure.request'
    assert 'x'*40 not in str(calls) and calls[0][1]['idempotency_key']=='explicit-one-line' and calls[0][1]['background'] is True


@pytest.mark.parametrize('authorization',[None,'Bearer wrong','Basic '+'x'*40,'Bearer \u00e9'])
def test_missing_or_invalid_operator_never_dispatches(monkeypatch,tmp_path,authorization):
    session,calls=client(monkeypatch,tmp_path)
    headers={'Idempotency-Key':'one'}
    if authorization and authorization.isascii():headers['Authorization']=authorization
    elif authorization:
        # The dependency also fails closed on non-ASCII input from a framework.
        with pytest.raises(HTTPException) as error:routes.require_exposure_operator(authorization)
        assert error.value.status_code==401
        assert not calls;return
    response=session.post('/api/learning/historical-exposure',json=payload(),headers=headers)
    assert response.status_code==401 and not calls


def test_unconfigured_or_public_credential_disables_feature_without_dispatch(monkeypatch,tmp_path):
    session,calls=client(monkeypatch,tmp_path,0o644)
    response=session.post('/api/learning/historical-exposure',json=payload(),
        headers={'Authorization':'Bearer '+'x'*40,'Idempotency-Key':'one'})
    assert response.status_code==503 and not calls
    monkeypatch.delenv('TEMPO_EXPOSURE_OPERATOR_TOKEN_FILE')
    assert session.post('/api/learning/historical-exposure',json=payload(),headers={'Idempotency-Key':'one'}).status_code==503


def test_bulk_or_unconfirmed_payload_is_rejected(monkeypatch,tmp_path):
    session,calls=client(monkeypatch,tmp_path)
    for body in ([payload()],{**payload(),'confirmation':'automatic'},{**payload(),'route_ids':['same','same']}):
        assert session.post('/api/learning/historical-exposure',json=body,
            headers={'Authorization':'Bearer '+'x'*40,'Idempotency-Key':'one'}).status_code==422
    assert not calls


@pytest.mark.parametrize('action',['cancel','refresh'])
def test_retained_intent_controls_require_operator_idempotency_and_cannot_assert_completion(monkeypatch,tmp_path,action):
    session,calls=client(monkeypatch,tmp_path)
    body=dict(target_id='target',workspace_id='legacy',card_id='physical',encounter_key='historical-exposure:'+'a'*64)
    url='/api/learning/historical-exposure/'+action
    assert session.post(url,json=body,headers={'Idempotency-Key':'control'}).status_code==401
    headers={'Authorization':'Bearer '+'x'*40,'Idempotency-Key':'control'}
    assert session.post(url,json={**body,'verified_normal_attempt':True},headers=headers).status_code==422
    assert session.post(url,json=body,headers={'Authorization':'Bearer '+'x'*40}).status_code==422
    assert not calls
    assert session.post(url,json=body,headers=headers).status_code==200
    assert calls==[(('historical_exposure.'+action,body),dict(idempotency_key='control'))]


def test_failed_retry_status_is_visible_and_never_falsely_served(monkeypatch,tmp_path):
    from datetime import datetime,timezone
    from tempo_learning_review import historical_exposure as practice
    from app.storage import historical_exposure as storage
    from app.services.historical_exposure_source import project_encounter
    session,calls=client(monkeypatch,tmp_path)
    selection,captured=fixture();verified=project_encounter(selection,captured)
    record=practice.Record(verified.request,datetime(2026,10,20,tzinfo=timezone.utc),'pending',
        practice.Placement('deferred','queue_eligibility_unavailable'),'2026-10-20')
    observed=[]
    monkeypatch.setattr(storage,'read_status',lambda scope,key:observed.append((scope,key)) or (record,'failed'))
    body=dict(target_id=record.card_scope().target_id,workspace_id='legacy',card_id=record.card_scope().card_id,
        encounter_key=record.request.encounter.key())
    response=session.post('/api/learning/historical-exposure/status',json=body,headers={'Authorization':'Bearer '+'x'*40})
    assert response.status_code==200 and response.json()['state']=='pending'
    assert response.json()['retry_required'] and response.json()['retry_state']=='failed'
    assert response.json()['completion_review_id'] is None and not calls
    assert observed==[(record.card_scope(),record.request.encounter.key())]


@pytest.mark.parametrize('suffix',['','/cancel','/refresh','/status'])
def test_full_api_cutover_reaches_operator_authorization_for_exact_practice_routes(monkeypatch,tmp_path,suffix):
    from app import main
    token=tmp_path/'operator';token.write_text('x'*40);token.chmod(0o600)
    monkeypatch.setenv('TEMPO_EXPOSURE_OPERATOR_TOKEN_FILE',str(token))
    monkeypatch.setattr(main.postgres_store,'configured',lambda:True)
    response=TestClient(main.app).post('/api/learning/historical-exposure'+suffix,
        json=payload() if not suffix else dict(target_id='target',workspace_id='legacy',card_id='card',encounter_key='historical-exposure:'+'a'*64),
        headers={'Idempotency-Key':'native-cutover'})
    assert response.status_code==401,response.text


def test_explicit_practice_request_is_allowed_in_study_only_background_commands():
    from app.services.study_mode import STUDY_BACKGROUND_COMMANDS
    assert 'historical_exposure.request' in STUDY_BACKGROUND_COMMANDS
