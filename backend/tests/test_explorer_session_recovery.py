"""Explorer session storage and safe recovery boundaries."""
from types import SimpleNamespace
from app.services import repertoire_coverage as coverage


def test_explorer_token_never_uses_persisted_broker(monkeypatch):
    broker_writes=[]
    monkeypatch.setattr(coverage.postgres_store,'configured',lambda:True)
    class PersistedBroker:
        def setex(self,*values): broker_writes.append(values)
    monkeypatch.setattr(coverage,'redis_client',lambda:PersistedBroker(),raising=False)
    dedicated=[]
    monkeypatch.setattr(coverage,'explorer_sessions',SimpleNamespace(register=lambda token:dedicated.append(token)),raising=False)
    coverage.set_explorer_session_token('synthetic-session-credential')
    assert broker_writes==[], 'Explorer credentials reached durable broker storage'
    assert dedicated==['synthetic-session-credential']


class SessionStore:
    def __init__(self):
        self.values={}
        self.configuration={'save':'','appendonly':'no'}
        self.expiry=None
    def config_get(self,*_): return self.configuration
    def mget(self,*keys): return [self.values.get(key) for key in keys]
    def delete(self,key): self.values.pop(key,None)
    def eval(self,script,count,token_key,rejected_key,token,digest,seconds):
        from app.services import explorer_sessions as sessions
        self.expiry=seconds
        if script==sessions._REGISTER:
            if self.values.get(rejected_key)==digest: return 0
            self.values[token_key]=token
            self.values.pop(rejected_key,None)
        else:
            if self.values.get(token_key) not in {None,token}: return 0
            if not self.values.get(token_key) and self.values.get(rejected_key) not in {None,digest}: return 0
            self.values.pop(token_key,None)
            self.values[rejected_key]=digest
        return 1


def test_explorer_status_is_safe_and_rejection_cannot_remove_replacement_session(monkeypatch):
    import pytest
    from app.services import explorer_sessions as sessions
    store=SessionStore()
    monkeypatch.setattr(sessions,'client',lambda:store)
    assert sessions.status()=={'status':'registration_missing'}
    sessions.register('first-synthetic')
    assert store.expiry==86400 and sessions.get_token()=='first-synthetic'
    assert sessions.status()=={'status':'available'}
    sessions.register('replacement-synthetic')
    assert not sessions.reject('first-synthetic')
    assert sessions.get_token()=='replacement-synthetic'
    assert sessions.reject('replacement-synthetic')
    assert sessions.status()=={'status':'credential_rejected'}
    with pytest.raises(sessions.ExplorerCredentialRejected): sessions.register('replacement-synthetic')
    sessions.register('reconnected-synthetic')
    assert sessions.status()=={'status':'available'}
    assert 'synthetic' not in str(sessions.status())


def test_explorer_store_rejects_persistence_before_writing_any_credential(monkeypatch):
    import pytest, redis
    from app.services import explorer_sessions as sessions
    store=SessionStore()
    monkeypatch.setattr(sessions,'client',lambda:store)
    for configuration in ({'save':'900 1','appendonly':'no'},{'save':'','appendonly':'yes'}):
        store.configuration=configuration
        with pytest.raises(redis.RedisError,match='Disable'): sessions.register('synthetic')
        assert store.values=={}


def test_explorer_store_requires_a_separate_server_not_another_broker_database(monkeypatch):
    import pytest, redis
    from app.services import explorer_sessions as sessions
    monkeypatch.setenv('TEMPO_REDIS_URL','redis://same-server:6379/0')
    monkeypatch.setenv('TEMPO_EXPLORER_SESSION_REDIS_URL','redis://same-server:6379/1')
    with pytest.raises(redis.RedisError,match='separate'): sessions.client()
    monkeypatch.delenv('TEMPO_EXPLORER_SESSION_REDIS_URL')
    with pytest.raises(redis.RedisError,match='Configure'): sessions.client()


def test_explorer_rejected_environment_credential_waits_for_reconnection(monkeypatch):
    from app.services import explorer_sessions as sessions
    store=SessionStore()
    monkeypatch.setattr(sessions,'client',lambda:store)
    monkeypatch.setenv('TEMPO_LICHESS_EXPLORER_TOKEN','environment-synthetic')
    assert sessions.get_token()=='environment-synthetic'
    assert sessions.reject('environment-synthetic')
    assert sessions.get_token() is None and sessions.status()=={'status':'credential_rejected'}
    monkeypatch.setenv('TEMPO_LICHESS_EXPLORER_TOKEN','new-environment-synthetic')
    assert sessions.get_token()=='new-environment-synthetic'


def test_explorer_status_endpoint_reports_unknown_when_store_is_unavailable(monkeypatch):
    from fastapi.testclient import TestClient
    import redis
    from app import main
    from app.services import explorer_sessions as sessions
    monkeypatch.setattr(main.postgres_store,'configured',lambda:True)
    def unavailable(): raise redis.ConnectionError('synthetic outage')
    monkeypatch.setattr(sessions,'client',unavailable)
    response=TestClient(main.app).get('/api/repertoire-coverage/explorer-session',headers={'X-Tempo-Work-Class':'background'})
    assert response.status_code==503 and 'unavailable' in response.json()['detail']
    assert 'available' not in response.json()


def test_explorer_status_and_registration_contract_retain_rejection_without_credentials(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.services import explorer_sessions as sessions
    store=SessionStore()
    monkeypatch.setattr(main.postgres_store,'configured',lambda:True)
    monkeypatch.setattr(sessions,'client',lambda:store)
    monkeypatch.delenv('TEMPO_REDIS_URL',raising=False)
    client=TestClient(main.app)
    path='/api/repertoire-coverage/explorer-session'
    assert client.get(path).json()=={'status':'registration_missing'}
    assert client.post(path,headers={'Authorization':'Bearer synthetic'}).json()=={'registered':True}
    assert client.get(path).json()=={'status':'available'}
    sessions.reject('synthetic')
    assert client.get(path).json()=={'status':'credential_rejected'}
    response=client.post(path,headers={'Authorization':'Bearer synthetic'})
    assert response.status_code==409 and response.json()['detail']['code']=='explorer_credential_rejected'
    assert 'synthetic' not in response.text
    assert client.post(path,headers={'Authorization':'Bearer changed-synthetic'}).json()=={'registered':True}


def test_explorer_invalid_store_address_is_unavailable_not_credential_rejection(monkeypatch):
    import pytest, redis
    from app.services import explorer_sessions as sessions
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    for invalid_address in ('redis://localhost:invalid/0', 'https://localhost', 'redis://'):
        monkeypatch.setenv('TEMPO_EXPLORER_SESSION_REDIS_URL', invalid_address)
        with pytest.raises(redis.RedisError, match='valid dedicated'):
            sessions.client()


def test_explorer_late_rejection_cannot_replace_another_credentials_rejected_fingerprint(monkeypatch):
    from app.services import explorer_sessions as sessions
    store=SessionStore()
    monkeypatch.setattr(sessions,'client',lambda:store)
    sessions.register('older-synthetic')
    sessions.register('newer-synthetic')
    assert sessions.reject('newer-synthetic')
    assert not sessions.reject('older-synthetic')
    assert store.values[sessions.REJECTED_KEY]==sessions.fingerprint('newer-synthetic')
