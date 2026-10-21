"""Request admission and provider-source composition with synthetic fake rows."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempo_intelligence.errors import DomainError
from tempo_intelligence.models import RequestInput
from tempo_intelligence.source_requests import cache_identity, request_descriptors, request_snapshot
from tempo_intelligence_database.adapters import store as store_module


NOW=datetime(2026,10,21,tzinfo=timezone.utc)
ORIGIN='https://synthetic.invalid'


def request():
    return RequestInput.model_validate_json((Path(__file__).parent.parent/'fixtures/request-v1.json').read_text())


def owner_with_script(steps,background=False):
    pending=deque(steps)
    events=[]
    class Database:
        active=False
        def execute(self,_query,parameters=()):
            assert self.active
            event,expected,row=pending.popleft()
            assert tuple(getattr(item,'obj',item) for item in parameters)==expected
            events.append(event)
            return SimpleNamespace(fetchone=lambda:row)
    database=Database()
    @contextmanager
    def transaction(**options):
        assert options==({'background':True} if background else {})
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
            if name in ('workspace_id','configuration','inspect','_append'):
                events.append('lookup-'+name)
            return super().__getattribute__(name)
    class Configuration:
        @property
        def provider_base_url(self):
            events.append('origin')
            return ORIGIN
    owner=Owner.__new__(Owner)
    owner.transaction=transaction
    owner.workspace_id='fixture'
    owner.configuration=Configuration()
    def inspect(identity):
        assert not database.active
        events.append('inspect')
        return {'request_id':identity}
    owner.inspect=inspect
    return owner,database,pending,events


def test_admission_retains_sorted_locks_expired_refresh_borrowed_effects_and_post_commit_inspect(monkeypatch):
    value=request()
    payload=value.model_dump(mode='json')
    snapshot,immutable,identity=request_snapshot(value)
    descriptors=sorted(request_descriptors(value,SimpleNamespace(provider_base_url=ORIGIN)),key=lambda item:cache_identity('fixture',item))
    steps=[('lock',('request:'+value.request_id,),None),('previous',(value.request_id,1),None),
        ('head',(value.request_id,),None),('snapshot-save',(identity,immutable),None),('snapshot-read',(identity,),{'payload':immutable})]
    bindings=[]
    expected=['lookup-workspace_id','enter','lock','previous','head','snapshot-save','snapshot-read','lookup-configuration','origin','origin']
    expected+=['lookup-workspace_id']*len(descriptors)
    caches={}
    for index,descriptor in enumerate(descriptors):
        cache_id=cache_identity('fixture',descriptor)
        cache={'cache_id':cache_id,'kind':descriptor['kind'],'state':'complete' if index==0 else 'queued','accepted_id':'accepted'}
        caches[cache_id]=cache
        current={**cache,'state':'queued'}
        steps.extend([('initialize-'+str(index),(cache_id,descriptor,snapshot,descriptor['kind'],'queued'),None),('lock-'+str(index),(cache_id,),cache)])
        expected+=['lookup-workspace_id']+(['profile'] if descriptor['kind']=='maia' else [])+['initialize-'+str(index),'lock-'+str(index)]
        if index==0:
            steps.append(('accepted',('accepted',),{'accepted_at':NOW-timedelta(seconds=value.max_age_seconds)}))
            expected+=['accepted','clock','generation']
        steps.append(('current-'+str(index),(cache_id,),current))
        expected+=['current-'+str(index)]+(['maia'] if descriptor['kind']=='maia' else [])
        bindings.append({'kind':descriptor['kind'],'cache_id':cache_id})
    steps.extend([('request-save',(value.request_id,1,payload,bindings),None),('head-save',(value.request_id,1),None)])
    expected+=['request-save','head-save','commit','lookup-inspect','inspect']
    owner,database,pending,events=owner_with_script(steps)
    def clock():
        assert database.active
        events.append('clock')
        return NOW
    def profile(connection,descriptor):
        assert connection is database and database.active and descriptor['kind']=='maia'
        events.append('profile')
    def generation(connection,cache):
        assert connection is database and database.active and caches[cache['cache_id']] is cache
        events.append('generation')
    def maia(connection,cache):
        assert connection is database and database.active and cache['kind']=='maia' and cache['state']=='queued'
        events.append('maia')
    owner._profile=profile
    owner._new_generation=generation
    owner._maia_job=maia
    monkeypatch.setattr(store_module,'utcnow',clock)
    assert owner.submit(value)=={'request_id':value.request_id}
    assert events==expected and not pending and not database.active


@pytest.mark.parametrize('case',['replay','conflict','snapshot-conflict'])
def test_admission_replay_guards_precede_cache_work_and_only_success_inspects(case):
    value=request()
    payload=value.model_dump(mode='json')
    snapshot,immutable,identity=request_snapshot(value)
    old=None if case=='snapshot-conflict' else {'payload':payload if case=='replay' else {}}
    steps=[('lock',('request:'+value.request_id,),None),('previous',(value.request_id,1),old)]
    if case=='snapshot-conflict':steps += [('head',(value.request_id,),None),('snapshot-save',(identity,immutable),None),('snapshot-read',(identity,),{'payload':{}})]
    owner,database,pending,events=owner_with_script(steps)
    if case=='replay':
        assert owner.submit(value)=={'request_id':value.request_id}
        assert events==['lookup-workspace_id','enter','lock','previous','commit','lookup-inspect','inspect']
    else:
        with pytest.raises(DomainError,match='snapshot_replay_conflict' if case=='snapshot-conflict' else 'request_replay_conflict'):
            owner.submit(value)
        assert events[-1]=='rollback' and 'lookup-inspect' not in events
    assert not pending and not database.active


@pytest.mark.parametrize('case',['no-control','cooldown','no-cache','missing-credential','claimed'])
def test_provider_claim_retains_control_clock_cache_credential_and_lease_order(monkeypatch,case):
    control=None if case=='no-control' else {'next_attempt_at':NOW+timedelta(seconds=1) if case=='cooldown' else None,'leased_until':None}
    cache=None if case=='no-cache' else {'cache_id':'cache'}
    credential={'status':'credential_missing' if case=='missing-credential' else 'accepted','registration_id':'synthetic','generation':2}
    claimed={'cache_id':'cache','lease_token':'synthetic-lease'}
    steps=[('control',(),control)]
    if case not in ('no-control','cooldown'):
        steps.append(('cache',(ORIGIN,),cache))
        if case=='missing-credential':steps.append(('wait',('credential_missing','cache'),None))
        if case=='claimed':steps += [('lease-cache',('synthetic-lease','cache'),claimed),('lease-provider',('synthetic-lease',),None)]
    owner,database,pending,events=owner_with_script(steps,background=True)
    def clock():
        assert database.active
        events.append('clock')
        return NOW
    def status(connection):
        assert connection is database and database.active
        events.append('credential')
        return credential
    def lease():
        assert database.active
        events.append('uuid')
        return SimpleNamespace(hex='synthetic-lease')
    owner._credential_status=status
    monkeypatch.setattr(store_module,'utcnow',clock)
    monkeypatch.setattr(store_module.uuid,'uuid4',lease)
    assert owner.claim()==({**claimed,'credential':credential} if case=='claimed' else None)
    expected=['enter','control','clock']
    if case not in ('no-control','cooldown'):expected+=['lookup-configuration','origin','cache']
    if case in ('missing-credential','claimed'):expected+=['credential']
    if case=='missing-credential':expected+=['wait']
    if case=='claimed':expected+=['uuid','lease-cache','lease-provider']
    assert events==expected+['commit'] and not pending and not database.active


@pytest.mark.parametrize('accepted',[False,True],ids=['stale-lease','publish'])
def test_publication_keeps_lock_fence_writer_lookup_uuid_clock_append_and_release_order(monkeypatch,accepted):
    claim={'cache_id':'cache','generation':1,'execution_generation':2,'lease_token':'lease','credential':{'registration_id':'synthetic','generation':3}}
    cache={'cache_id':'cache'} if accepted else None
    steps=[('control',(),None),('fence',('cache',1,2,'lease'),cache)]
    if accepted:steps += [('credential-accepted',('synthetic',3),None),('release',(None,'lease'),None)]
    owner,database,pending,events=owner_with_script(steps,background=True)
    def clock():
        assert database.active
        events.append('clock')
        return NOW
    def retrieval():
        assert database.active
        events.append('uuid')
        return SimpleNamespace(hex='retrieval')
    def append(connection,captured,payload):
        assert connection is database and database.active and captured is cache
        assert payload=={'source':'synthetic','retrieval_id':'retrieval','retrieved_at':NOW.isoformat()}
        events.append('append')
    owner._append=append
    monkeypatch.setattr(store_module,'utcnow',clock)
    monkeypatch.setattr(store_module.uuid,'uuid4',retrieval)
    assert owner.publish(claim,{'source':'synthetic'}) is accepted
    expected=['enter','control','fence']
    if accepted:expected+=['lookup-_append','uuid','clock','append','credential-accepted','release']
    assert events==expected+['commit'] and not pending and not database.active
