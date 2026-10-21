"""Pure request/cache policy and response serialization; synthetic facts only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempo_intelligence.models import RequestInput
from tempo_intelligence.serialization import public
from tempo_intelligence.source_requests import cache_identity, request_descriptors, retry_eligible


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
