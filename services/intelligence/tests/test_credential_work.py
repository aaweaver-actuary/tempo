"""Credential workflow source boundaries with fake rows and substituted loading."""
import builtins
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tempo_intelligence.errors import DomainError
from tempo_intelligence_database.adapters import store as store_module


NOW=datetime(2026,10,21,tzinfo=timezone.utc)


def owner_with_script(steps,read_only=False):
    pending=deque(steps)
    events=[]
    class Database:
        active=False
        def execute(self,_query,parameters=()):
            assert self.active
            event,expected,row=pending.popleft()
            assert tuple(parameters)==expected
            events.append(event)
            return SimpleNamespace(fetchone=lambda:row)
    database=Database()
    @contextmanager
    def transaction(**options):
        assert options==({'read_only':True} if read_only else {})
        events.append('enter')
        database.active=True
        try:
            yield database
        except DomainError:
            events.append('rollback')
            raise
        else:
            events.append('commit')
        finally:
            database.active=False
    class Owner(store_module.Store):
        def __getattribute__(self,name):
            if name=='configuration':events.append('configuration')
            return super().__getattribute__(name)
    owner=Owner.__new__(Owner)
    owner.transaction=transaction
    return owner,database,pending,events


@pytest.mark.parametrize('case',['missing','expired','rejected'])
def test_row_status_keeps_missing_short_circuit_and_clock_before_terminal_status_guard(monkeypatch,case):
    row={'generation':2,'registration_id':None if case=='missing' else 'synthetic','status':'rejected' if case=='rejected' else 'registered','expires_at':NOW-timedelta(seconds=1)}
    owner,database,pending,events=owner_with_script([('row',(),row)],read_only=True)
    def clock():
        assert database.active
        events.append('clock')
        return NOW
    monkeypatch.setattr(store_module,'utcnow',clock)
    with owner.transaction(read_only=True):result=owner._credential_status(database)
    if case=='missing':
        assert result=={'generation':2,'registration_id':None,'status':'credential_missing'}
    else:
        assert result=={**row,'expires_at':row['expires_at'].isoformat(),'status':'expired' if case=='expired' else 'rejected'}
    assert events==['enter','row']+([] if case=='missing' else ['clock'])+['commit']
    assert not pending and not database.active


@pytest.mark.parametrize('case',['inactive','active','load-error'])
def test_status_keeps_late_import_config_and_substituted_load_after_read_context(monkeypatch,case):
    # Import source definitions for substitution only; the real loader is never called.
    from tempo_intelligence import credentials
    status='revoked' if case=='inactive' else 'accepted'
    row={'generation':3,'registration_id':'synthetic','status':status,'expires_at':None}
    owner,database,pending,events=owner_with_script([('row',(),row)],read_only=True)
    sentinel=object()
    owner.configuration=SimpleNamespace(credential_file=sentinel)
    def load():
        assert not database.active
        events.append('load')
        if case=='load-error':raise credentials.CredentialError('synthetic_error')
        return SimpleNamespace(registration_id='synthetic',expires_at=NOW+timedelta(seconds=1))
    def loader(path):
        assert path is sentinel and not database.active
        events.append('construct-loader')
        return SimpleNamespace(load=load)
    def clock():
        assert not database.active
        events.append('clock')
        return NOW
    original_import=builtins.__import__
    def observe_import(name,*arguments,**options):
        if name=='tempo_intelligence.credentials':
            assert not database.active
            events.append('late-import')
        return original_import(name,*arguments,**options)
    monkeypatch.setattr(credentials,'PrivateCredentials',loader)
    monkeypatch.setattr(store_module,'utcnow',clock)
    monkeypatch.setattr(builtins,'__import__',observe_import)
    result=owner.credential_status()
    expected=['enter','row','commit']
    if case!='inactive':expected+=['late-import','configuration','construct-loader','load']
    if case=='active':expected+=['clock']
    assert events==expected and not pending and not database.active
    assert result=={**row,'status':'credential_storage_unavailable' if case=='load-error' else status}


@pytest.mark.parametrize('case',['new','replay','generation-conflict'])
def test_reload_keeps_generation_and_replay_guards_and_registration_write_order(monkeypatch,case):
    expiry=NOW+timedelta(seconds=1)
    class Material:
        registration_id='synthetic'
        @property
        def expires_at(self):
            assert case=='new','replay/conflict must not consult incoming expiry'
            return expiry
    material=Material()
    head={'generation':3 if case=='generation-conflict' else 2,'registration_id':'synthetic' if case=='replay' else None}
    old={'status':'accepted'} if case=='replay' else None
    row={'generation':2 if case=='replay' else 3,'registration_id':'synthetic','status':'accepted' if case=='replay' else 'registered','expires_at':None}
    steps=[('head',(),head)]
    if case!='generation-conflict':
        steps.append(('registration',('synthetic',),old))
        if case=='new':steps += [('register',('synthetic',3,expiry),None),('activate',(3,'synthetic'),None),('resume',(),None)]
        steps.append(('row',(),row))
    owner,database,pending,events=owner_with_script(steps)
    def clock():
        assert database.active
        events.append('clock')
        return NOW
    monkeypatch.setattr(store_module,'utcnow',clock)
    if case=='generation-conflict':
        with pytest.raises(DomainError,match='credential_generation_conflict'):owner.credential_reload(material,2)
        assert events==['enter','head','rollback']
    else:
        assert owner.credential_reload(material,2)==row
        expected=['enter','head','registration']+(['clock','register','activate','resume'] if case=='new' else [])+['row','commit']
        assert events==expected
    assert not pending and not database.active


@pytest.mark.parametrize('registered',[False,True],ids=['no-registration','revoke-current'])
def test_revoke_keeps_locked_head_optional_write_and_status_return_in_same_transaction(registered):
    identity='synthetic' if registered else None
    head={'generation':2,'registration_id':identity}
    row={'generation':2,'registration_id':identity,'status':'revoked','expires_at':None}
    steps=[('head',(),head)]
    if registered:steps.append(('revoke',('synthetic',),None))
    steps.append(('row',(),row))
    owner,database,pending,events=owner_with_script(steps)
    result=owner.credential_revoke(2)
    assert result==(row if registered else {'generation':2,'registration_id':None,'status':'credential_missing'})
    assert events==['enter','head']+(['revoke'] if registered else [])+['row','commit']
    assert not pending and not database.active
