"""Games viewing is enforced by the service, not just hidden UI controls."""
from dataclasses import replace
from fastapi.testclient import TestClient
import pytest
from tempo_intelligence import create_app
from tempo_intelligence import Configuration


def configuration(**changes):
    return Configuration(**(dict(workspace_id="fixture",database_url="postgresql://unused/intelligence",caller_token="c"*40,operator_token="o"*40,worker_token="w"*40,worker_enabled=False)|changes))


def authorization(scope="caller"):
    return {"Authorization":"Bearer "+{'caller':'c','operator':'o','worker':'w'}[scope]*40}


class GamesStore:
    workspace_id="fixture"
    read_only=True
    def __init__(self):self.calls=[]
    def validate_startup(self):self.calls.append(('startup',))
    def health(self):pass


def test_games_readonly_configuration_refuses_workers_and_startup_profile_writes():
    value=configuration(read_only=True)
    assert value.read_only and not value.worker_enabled
    with pytest.raises(ValueError,match='read_only_workers_disabled'):
        replace(value,worker_enabled=True)
    with pytest.raises(ValueError,match='read_only_profiles_unavailable'):
        replace(value,model_profiles=({'model_id':'model','model_version':'1','artifact_sha256':'a'*64,'ratings':[1500]},))


@pytest.mark.parametrize('method,path,scope',[
    ('POST','/v1/analysis/runs','caller'),('POST','/v1/analysis/inputs','operator'),
    ('POST','/v1/analysis/inputs/pages','operator'),('POST','/v1/analysis/runs/run/commands','caller'),
    ('POST','/v1/analysis/findings/finding/commands','caller'),('POST','/v1/requests','caller'),
    ('POST','/v1/maia/results','worker'),('POST','/v1/credentials/lichess-explorer/reload','operator'),
    ('PUT','/v1/analysis/runs/run','caller'),('PATCH','/v1/analysis/runs/run','caller'),
    ('DELETE','/v1/analysis/runs/run','caller'),
])
def test_games_readonly_rejects_all_mutations_before_payload_or_storage(method,path,scope):
    store=GamesStore()
    with TestClient(create_app(configuration(read_only=True),store=store)) as client:
        assert client.request(method,path,headers=authorization(scope),json={'unsafe':'not validated'}).status_code==405
        assert store.calls==[('startup',)]


def test_games_readonly_preserves_authentication_workspace_and_advertises_capability():
    store=GamesStore()
    with TestClient(create_app(configuration(read_only=True),store=store)) as client:
        assert client.post('/v1/analysis/runs',json={}).status_code==401
        assert client.post('/v1/analysis/runs',json={},headers={**authorization(),'X-Tempo-Workspace':'other'}).status_code==403
        response=client.get('/v1/analysis/bootstrap',headers=authorization())
        assert response.status_code==200 and 'games-readonly-v1' in response.json()['capabilities']
        assert store.calls==[('startup',)]


def test_games_readonly_never_starts_optional_worker(monkeypatch):
    from tempo_intelligence import provider,worker
    monkeypatch.setattr(provider,'ExplorerClient',lambda *a,**k:pytest.fail('No provider initialized for viewing'))
    monkeypatch.setattr(worker,'Worker',lambda *a,**k:pytest.fail('No worker initialized for viewing'))
    with TestClient(create_app(configuration(read_only=True),store=GamesStore())) as client:
        assert client.get('/healthz').status_code==200


def test_games_readonly_refuses_mutable_store_or_injected_worker_before_startup():
    store=GamesStore();store.read_only=False
    with pytest.raises(ValueError,match='read_only_store_required'):
        create_app(configuration(read_only=True),store=store)
    assert store.calls==[]
    with pytest.raises(ValueError,match='read_only_workers_disabled'):
        create_app(configuration(read_only=True),store=GamesStore(),worker=object())


def test_games_readonly_environment_defaults_workers_off_and_rejects_explicit_on(monkeypatch,tmp_path):
    values={'DATABASE_URL':'postgresql://unused/intelligence','CALLER_TOKEN':'c'*40,'OPERATOR_TOKEN':'o'*40,'WORKER_TOKEN':'w'*40}
    for name,value in values.items():
        path=tmp_path/name;path.write_text(value);path.chmod(0o600)
        monkeypatch.setenv('TEMPO_INTELLIGENCE_'+name+'_FILE',str(path))
    monkeypatch.setenv('TEMPO_INTELLIGENCE_WORKSPACE','fixture')
    monkeypatch.setenv('TEMPO_INTELLIGENCE_READ_ONLY','1')
    monkeypatch.delenv('TEMPO_INTELLIGENCE_WORKER_ENABLED',raising=False)
    value=Configuration.environment();assert value.read_only and not value.worker_enabled
    monkeypatch.setenv('TEMPO_INTELLIGENCE_WORKER_ENABLED','1')
    with pytest.raises(ValueError,match='read_only_workers_disabled'):Configuration.environment()
