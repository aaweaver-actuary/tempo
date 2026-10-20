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
    assert 'x'*40 not in str(calls) and calls[0][1]['idempotency_key']=='explicit-one-line'


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
