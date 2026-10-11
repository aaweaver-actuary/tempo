"""Independent audit-only HTTP contract cases; no product changes."""
import copy
import pytest
from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError
from app import main

@pytest.mark.parametrize('configured',[False,True])
def test_audit187_status_unknown_contract(configured,monkeypatch):
    monkeypatch.setattr(main.postgres_store,'configured',lambda:configured)
    monkeypatch.setattr(main,'read_operation',lambda operation_id,**options:{'operation_id':operation_id,'state':'unknown'})
    response=TestClient(main.app).get('/api/operations/audit187-original')
    assert response.status_code==(200 if configured else 404)
    if configured: assert response.json()=={'operation_id':'audit187-original','state':'unknown'}

def test_audit187_retry_409_does_not_dispatch_when_operation_no_longer_blocked(monkeypatch):
    monkeypatch.setattr(main.postgres_store,'configured',lambda:True)
    monkeypatch.setattr(main,'load_blocked_operation',lambda _operation_id:None)
    monkeypatch.setattr(main.celery_app,'send_task',lambda *_args,**_options:pytest.fail('Nonblocked operation dispatched'))
    response=TestClient(main.app).post('/api/operations/audit187-original/retry')
    assert response.status_code==409
    assert response.json()['detail']=='Only a durable blocked operation can be retried'

@pytest.mark.parametrize('lost_broker',[False,True])
def test_audit187_retry_acknowledgment_contract_preserves_original_envelope(monkeypatch,lost_broker):
    saved={'operation_id':'audit187-original','command_name':'repertoire.prefix_transition.apply',
           'payload':{'operation_id':'audit187-original','repertoire_id':'audit-rep','request':{'plan_id':'audit-plan'}},
           'background':False,'retry_cycle':7}
    before=copy.deepcopy(saved)
    sent=[]
    monkeypatch.setattr(main.postgres_store,'configured',lambda:True)
    monkeypatch.setattr(main,'load_blocked_operation',lambda _operation_id:saved)
    def send(name,**options):
        sent.append((name,options))
        if lost_broker: raise OperationalError('Broker unavailable')
    monkeypatch.setattr(main.celery_app,'send_task',send)
    response=TestClient(main.app).post('/api/operations/audit187-original/retry')
    assert response.status_code==(503 if lost_broker else 202)
    assert saved==before
    assert len(sent)==1
    assert sent[0][0]=='app.tasks.execute_foreground_command'
    assert sent[0][1]['args']==['audit187-original',saved['command_name'],before['payload'],7]
    assert sent[0][1]['task_id']=='audit187-original'
    if lost_broker: assert response.json()['detail']=='Save queue unavailable; blocked operation remains durable'
    else: assert response.json()=={'operation_id':'audit187-original','state':'blocked','message':'Retry requested with the original operation identity and payload'}
