"""Pure request/cache policy and response serialization; synthetic facts only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempo_intelligence.models import RequestInput
from tempo_intelligence.errors import DomainError
from tempo_intelligence.serialization import public
from tempo_intelligence.source_requests import (admit_request, cache_identity, failure_transition,
    request_descriptors, request_snapshot, retry_eligible, source_inspection, validate_request_revision)


def request():
    path=Path(__file__).resolve().parents[1]/'fixtures/request-v1.json'
    return RequestInput.model_validate_json(path.read_text())


def test_public_values_convert_nested_datetimes_without_mutating_source():
    when=datetime(2026,10,21,1,2,3,tzinfo=timezone.utc)
    unchanged=('tuple',when)
    value={'items':[when,{'next':when,'optional':None,'flag':False,'count':2}], 'unchanged':unchanged}
    expected={'items':['2026-10-21T01:02:03+00:00',{'next':'2026-10-21T01:02:03+00:00',
        'optional':None,'flag':False,'count':2}],'unchanged':unchanged}
    assert public(value)==expected
    assert value['items'][0] is when and value['items'][1]['next'] is when
    assert public(unchanged) is unchanged


def test_request_descriptors_preserve_source_order_and_stamp_only_provider_sources():
    value=request()
    configuration=SimpleNamespace(provider_base_url='https://synthetic.invalid')
    descriptors=request_descriptors(value,configuration)
    assert [descriptor['kind'] for descriptor in descriptors]==['lichess','masters','maia']
    assert [descriptor['provider_origin'] for descriptor in descriptors[:2]]==[configuration.provider_base_url]*2
    assert 'provider_origin' not in descriptors[2]
    assert value.sources[0].kind=='lichess' and value.sources[2].kind=='maia'
    reversed_request=value.model_copy(update={'sources':list(reversed(value.sources))})
    reversed_descriptors=request_descriptors(reversed_request,configuration)
    assert sorted(descriptors,key=lambda descriptor:cache_identity('fixture',descriptor))==sorted(
        reversed_descriptors,key=lambda descriptor:cache_identity('fixture',descriptor))


def test_cache_identity_keeps_workspace_provider_and_semantics_but_ignores_legal_digest():
    descriptor=request_descriptors(request(),SimpleNamespace(provider_base_url='https://synthetic.invalid'))[0]
    original=cache_identity('fixture',descriptor)
    altered=deepcopy(descriptor)
    altered['legal_universe_digest']='sha256:'+'f'*64
    assert cache_identity('fixture',altered)==original
    assert cache_identity('other-workspace',descriptor)!=original
    altered['provider_origin']='https://different.invalid'
    assert cache_identity('fixture',altered)!=original
    altered=deepcopy(descriptor)
    altered['parameters']['ratings']='2000'
    assert cache_identity('fixture',altered)!=original


def test_maia_and_ineligible_cache_paths_do_not_read_provider_configuration():
    class UnusedProvider:
        @property
        def provider_base_url(self):
            raise AssertionError('provider lookup must retain its original short circuit')
    configuration=UnusedProvider()
    value=request()
    value=value.model_copy(update={'sources':[value.sources[2]]})
    assert request_descriptors(value,configuration)[0]['kind']=='maia'
    now=datetime(2026,10,21,tzinfo=timezone.utc)
    cache={'state':'failed','retryable':True,'next_attempt_at':None,'kind':'maia','descriptor':{}}
    assert retry_eligible(cache,now,configuration) is True
    assert retry_eligible({**cache,'state':'queued','kind':'lichess'},now,configuration) is False
    assert retry_eligible({**cache,'kind':'lichess','retryable':False},now,configuration) is False
    assert retry_eligible({**cache,'kind':'lichess','next_attempt_at':now+timedelta(seconds=1)},now,configuration) is False


@pytest.mark.parametrize('state,retryable,delay,kind,origin,expected',[
    ('failed',True,None,'lichess','configured',True),
    ('waiting_credentials',True,None,'masters','configured',True),
    ('retry_wait',True,0,'lichess','configured',True),
    ('retry_wait',True,-1,'lichess','configured',True),
    ('retry_wait',True,1,'lichess','configured',False),
    ('failed',False,None,'lichess','configured',False),
    ('failed',True,None,'lichess','unavailable',False),
    ('failed',True,None,'maia',None,True),
    ('queued',True,None,'lichess','configured',False),
    ('running',True,None,'lichess','configured',False),
    ('terminal',True,None,'lichess','configured',False),
    ('complete',True,None,'lichess','configured',False),
    ('awaiting_result',True,None,'maia',None,False),
])
def test_retry_eligibility_preserves_state_deadline_and_provider_fences(state,retryable,delay,kind,origin,expected):
    now=datetime(2026,10,21,tzinfo=timezone.utc)
    configuration=SimpleNamespace(provider_base_url='configured')
    cache={'state':state,'retryable':retryable,'next_attempt_at':None if delay is None else now+timedelta(seconds=delay),
           'kind':kind,'descriptor':{} if origin is None else {'provider_origin':origin}}
    before=deepcopy(cache)
    assert retry_eligible(cache,now,configuration) is expected
    assert cache==before
    from tempo_intelligence_database.adapters.store import Store
    owner=Store.__new__(Store)
    owner.configuration=configuration
    assert owner._retry_eligible(cache,now) is expected


def test_request_admission_checks_workspace_before_serializing_payload():
    value=request()
    assert admit_request(value,'fixture')==value.model_dump(mode='json')
    class WrongWorkspace:
        snapshot=SimpleNamespace(workspace_id='other-workspace')
        def model_dump(self,**options):
            raise AssertionError('rejected workspace must not serialize a payload')
    with pytest.raises(DomainError,match='workspace_mismatch') as error:
        admit_request(WrongWorkspace(),'fixture')
    assert error.value.status==403


@pytest.mark.parametrize('head,expected,revision,accepted',[
    (None,None,1,True),(None,0,1,True),(None,2,3,False),
    ({'revision':2},2,3,True),({'revision':2},2,2,False),({'revision':2},1,3,False),
])
def test_request_revision_admission_keeps_initial_and_replacement_fences(head,expected,revision,accepted):
    value=SimpleNamespace(expected_revision=expected,request_revision=revision)
    if accepted:
        assert validate_request_revision(value,head) is None
    else:
        with pytest.raises(DomainError,match='request_revision_conflict'):
            validate_request_revision(value,head)


def test_snapshot_binding_retains_original_bytes_and_excludes_only_policy_context():
    value=request()
    snapshot,immutable,identity=request_snapshot(value)
    assert snapshot==value.snapshot.model_dump(mode='json')
    assert immutable=={key:item for key,item in snapshot.items() if key!='policy_context'}
    assert 'policy_context' in snapshot and 'policy_context' not in immutable
    changed=value.model_copy(update={'snapshot':value.snapshot.model_copy(update={'policy_context':{'policy_id':'other'}})})
    assert request_snapshot(changed)[1:]==(immutable,identity)
    changed=value.model_copy(update={'snapshot':value.snapshot.model_copy(update={'publication_generation':2})})
    assert request_snapshot(changed)[2]!=identity


@pytest.mark.parametrize('age,freshness',[(None,'absent'),(9,'current'),(10,'stale')])
def test_source_inspection_keeps_freshness_boundary_and_clock_retry_order(age,freshness):
    now=datetime(2026,10,21,tzinfo=timezone.utc)
    cache={'kind':'maia','cache_id':'cache','generation':2,'execution_generation':3,
        'state':'retry_wait','accepted_id':'accepted' if age is not None else None,'error_code':None,
        'attempts':1,'retryable':True,'next_attempt_at':now}
    evidence={'accepted_at':now-timedelta(seconds=age)} if age is not None else None
    payload={'payload':{'max_age_seconds':10}} if evidence else {}
    events=[]
    times=iter([now,now+timedelta(microseconds=1)])
    def clock():
        value=next(times)
        events.append(('clock',value))
        return value
    def retry(captured,when):
        assert captured is cache
        events.append(('retry',when))
        return True
    source=source_inspection(cache,evidence,payload,retry,clock)
    assert source['freshness']==freshness and source['retry_eligible'] is True
    assert source['work_generation']==2 and source['execution_generation']==3
    assert source['evidence_id']==cache['accepted_id'] and source['retry_at'] is now
    expected_times=[now,now+timedelta(microseconds=1)] if evidence else [now]
    assert events==[('clock',value) for value in expected_times]+[('retry',expected_times[-1])]


@pytest.mark.parametrize('attempts,code,retryable,provider_delay,auth_failed,cancelled,expected',[
    (0,'provider_unavailable',True,False,False,False,(1,'retry_wait',5,True)),
    (1,'provider_unavailable',True,False,False,False,(2,'retry_wait',30,True)),
    (2,'provider_unavailable',True,False,False,False,(3,'failed',None,True)),
    (0,'invalid_input',False,False,False,False,(1,'failed',None,False)),
    (2,'rate_limited',False,True,False,False,(2,'retry_wait',120,True)),
    (2,'credential_missing',False,False,False,False,(2,'waiting_credentials',60,True)),
    (2,'provider_rejected',False,False,True,False,(2,'waiting_credentials',60,True)),
    (2,'cancelled',False,False,False,True,(2,'queued',None,True)),
])
def test_failure_transition_retains_attempt_budget_deadlines_and_cancellation(attempts,code,retryable,provider_delay,auth_failed,cancelled,expected):
    now=datetime(2026,10,21,tzinfo=timezone.utc)
    retry_at=now+timedelta(seconds=120) if provider_delay else None
    calls=[]
    def clock():
        calls.append('clock')
        return now
    actual=failure_transition({'attempts':attempts},code,retryable,retry_at,auth_failed,cancelled,clock)
    count,state,delay,can_retry=expected
    assert actual==(count,state,now+timedelta(seconds=delay) if delay is not None else None,can_retry)
    assert calls==(['clock'] if delay is not None and not provider_delay else [])
