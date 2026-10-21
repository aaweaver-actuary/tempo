"""Maia/evidence workflow composition with fixed fake rows, without a model or DB."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tempo_analysis_jobs_contracts.v1 import JobStatus, MaiaResult

from tempo_intelligence import maia_work, source_requests
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import source_descriptor
from tempo_intelligence.models import RequestInput
from tempo_intelligence_database.adapters.store import Store


def fixture(name):
    return json.loads((Path(__file__).parent.parent/'fixtures'/name).read_text())


def owner_with_script(steps,read_only=False):
    pending=deque(steps)
    events=[]
    class Database:
        active=False
        def execute(self,_query,parameters=()):
            assert self.active
            event,expected,row=pending.popleft()
            assert tuple(getattr(item,'obj',item) for item in parameters)==expected
            events.append(event)
            return SimpleNamespace(fetchone=lambda:row,fetchall=lambda:row)
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
    class Owner(Store):
        def __getattribute__(self,name):
            if name in ('workspace_id','_append'):
                events.append('lookup-'+name)
            return super().__getattribute__(name)
    owner=Owner.__new__(Owner)
    owner.transaction=transaction
    owner.workspace_id='fixture'
    return owner,database,pending,events


@pytest.mark.parametrize('configured',[True,False])
def test_profile_configuration_is_evaluated_before_fixed_read_even_when_rejected(configured):
    descriptor={'model_id':'maia-2','model_version':'fixture-1','artifact_sha256':'a'*64,'rating':1500}
    profile={key:value for key,value in descriptor.items() if key!='rating'}|{'ratings':[1500]}
    owner,database,pending,events=owner_with_script([('profile',('maia-2','fixture-1'),profile)])
    class Configuration:
        @property
        def model_profiles(self):
            events.append('profiles')
            return [profile] if configured else []
    owner.configuration=Configuration()
    if configured:
        with owner.transaction():owner._profile(database,descriptor)
    else:
        with pytest.raises(DomainError,match='unsupported_model_context'):
            with owner.transaction():owner._profile(database,descriptor)
    assert events==['enter','profiles','profile','commit' if configured else 'rollback']
    assert not pending and not database.active


@pytest.mark.parametrize('previous',[False,True],ids=['new-work','immutable-retry'])
def test_job_creation_borrows_transaction_and_retries_keep_persisted_constraints(monkeypatch,previous):
    request=RequestInput.model_validate(fixture('request-v1.json'))
    descriptor=source_descriptor(request.snapshot,request.sources[2])
    original=fixture('maia-job-v1.json')
    original['constraints']['max_memory_mb']=512
    cache={'cache_id':'cache','generation':1,'execution_generation':1 if previous else 0,'descriptor':descriptor}
    expected={**original,'execution_generation':2} if previous else maia_work.build_maia_job(descriptor,'fixture','cache',1,1).model_dump(mode='json')
    owner,database,pending,events=owner_with_script([
        ('previous',('cache',1),{'payload':original} if previous else None),
        ('insert',(expected['job_id'],1,expected['execution_generation'],'cache',expected),None),
        ('awaiting',(expected['execution_generation'],'cache'),None)])
    current=maia_work.build_maia_job
    def build(*arguments):
        assert database.active
        events.append('build')
        return current(*arguments)
    monkeypatch.setattr(maia_work,'build_maia_job',build)
    with owner.transaction():owner._maia_job(database,cache)
    assert events==['enter','previous']+([] if previous else ['lookup-workspace_id','build'])+['insert','awaiting','commit']
    assert not pending and not database.active


def test_job_list_keeps_binding_order_in_one_owner_read_snapshot():
    bindings=[{'kind':'maia','cache_id':'z'},{'kind':'lichess','cache_id':'skip'},{'kind':'maia','cache_id':'a'}]
    owner,database,pending,events=owner_with_script([
        ('head',('request',),{'bindings':bindings}),
        ('jobs-z',('z',),[{'payload':{'job_id':'z1'}},{'payload':{'job_id':'z2'}}]),
        ('jobs-a',('a',),[{'payload':{'job_id':'a'}}])],read_only=True)
    assert owner.maia_jobs('request')=={'schema_version':1,'jobs':[{'job_id':'z1'},{'job_id':'z2'},{'job_id':'a'}]}
    assert events==['enter','head','jobs-z','jobs-a','commit']
    assert not pending and not database.active


def test_evidence_envelope_keeps_workspace_lookup_and_serialization_inside_read(monkeypatch):
    accepted=datetime(2026,10,21,tzinfo=timezone.utc)
    owner,database,pending,events=owner_with_script([
        ('evidence',('accepted',),{'evidence_id':'accepted','accepted_at':accepted})],read_only=True)
    public=source_requests.public
    def serialize(value):
        assert database.active
        events.append('serialize')
        return public(value)
    monkeypatch.setattr(source_requests,'public',serialize)
    assert owner.evidence('accepted')=={'schema_version':1,'workspace_id':'fixture','evidence_id':'accepted','accepted_at':accepted.isoformat()}
    assert events==['enter','evidence','lookup-workspace_id','serialize','commit']
    assert not pending and not database.active


@pytest.mark.parametrize('replay',[False,True],ids=['accept','replay'])
def test_result_acceptance_keeps_wire_before_transaction_and_append_before_completion(monkeypatch,replay):
    result=MaiaResult.model_validate(fixture('maia-result-v1.json'))
    wire=result.model_dump(mode='json')
    identity=(result.job_id,result.request_revision,result.execution_generation)
    job={'payload':fixture('maia-job-v1.json'),'cache_id':'cache','status':'running','result_payload':wire if replay else None,'evidence_id':'accepted' if replay else None}
    owner,database,pending,events=owner_with_script([])
    class Descriptor(dict):
        def __getitem__(self,key):
            events.append('descriptor-'+key)
            return super().__getitem__(key)
    cache={'cache_id':'cache','generation':1,'execution_generation':1,'state':'awaiting_result','descriptor':Descriptor(artifact_sha256='a'*64)}
    pending.append(('job',identity,job))
    if not replay:
        pending.extend([('cache',('cache',),cache),('complete',(wire,'accepted',*identity),None)])
    def append(connection,captured,payload):
        assert connection is database and database.active and captured is cache
        assert payload=={'schema_version':1,'source':'maia','job':job['payload'],'result':wire,'artifact_sha256':'a'*64,'limitations':['v1_artifact_identity_is_configured_version_binding']}
        events.append('append')
        return 'accepted'
    owner._append=append
    dump=MaiaResult.model_dump
    def serialize(value,*arguments,**options):
        events.append(('wire',database.active))
        return dump(value,*arguments,**options)
    monkeypatch.setattr(MaiaResult,'model_dump',serialize)
    assert owner.accept_maia(result)=={'schema_version':1,'evidence_id':'accepted','replayed':replay}
    assert events[:3]==['lookup-workspace_id',('wire',False),'enter']
    expected=['lookup-workspace_id','enter','job']
    expected+=[] if replay else ['cache','lookup-_append','descriptor-artifact_sha256','append','complete']
    assert [event for event in events if not isinstance(event,tuple)]==expected+['commit']
    assert all(event[1] for event in events[3:] if isinstance(event,tuple))
    assert not pending and not database.active


@pytest.mark.parametrize('case',['running','failed','replay'])
def test_status_wire_follows_job_lock_and_replay_skips_cache_and_writes(monkeypatch,case):
    source=fixture('maia-job-v1.json')
    status=JobStatus.model_validate({**{key:source[key] for key in ('contract_version','workspace_id','job_id','request_revision','execution_generation')},'status':'running' if case=='replay' else case,'retryable':case=='failed','reason':'fixture_failure' if case=='failed' else None})
    wire=status.model_dump(mode='json')
    identity=(status.job_id,status.request_revision,status.execution_generation)
    job={'cache_id':'cache','status':'pending','status_payload':wire if case=='replay' else None,'evidence_id':None}
    steps=[('job',identity,job)]
    if case!='replay':
        steps.extend([('cache',('cache',),{'cache_id':'cache','generation':1,'execution_generation':1}),('update',(status.status,wire,*identity),None)])
        if case=='failed':steps.append(('fail',('maia_failed',True,'cache'),None))
    owner,database,pending,events=owner_with_script(steps)
    dump=JobStatus.model_dump
    def serialize(value,*arguments,**options):
        assert database.active and events[-1]=='job'
        events.append('wire')
        return dump(value,*arguments,**options)
    monkeypatch.setattr(JobStatus,'model_dump',serialize)
    assert owner.maia_status(status)=={'schema_version':1,'status':status.status}
    expected=['lookup-workspace_id','enter','job','wire']
    if case!='replay':expected+=['cache','update']+(['fail'] if case=='failed' else [])
    assert events==expected+['commit']
    assert not pending and not database.active
