"""Request-command composition with fixed fake-driver responses; no native DB."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tempo_intelligence.errors import DomainError
from tempo_intelligence_database.adapters import store as store_module


def owner_with_script(steps,head):
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
        assert options=={}
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
    def request(connection,identity):
        assert connection is database and database.active and identity=='request'
        events.append('head')
        return head
    owner=store_module.Store.__new__(store_module.Store)
    owner.transaction=transaction
    owner._head=request
    return owner,database,pending,events


@pytest.mark.parametrize('retry',[False,True],ids=['refresh','retry'])
def test_complete_command_keeps_sorted_locks_source_local_effects_and_receipt_last(monkeypatch,retry):
    due={'cache_id':'a-due','state':'failed','kind':'maia'}
    blocked={'cache_id':'b-blocked','state':'running','kind':'lichess'}
    terminal={'cache_id':'c-terminal','state':'terminal','kind':'maia'}
    queued={**due,'state':'queued'}
    payload={'request_id':'request','expected_revision':4,'operation':'retry' if retry else 'refresh'}
    result={'schema_version':1,'request_id':'request','request_revision':4,'command_id':'owned','accepted':True}
    steps=[('lock',('command:owned',),None),('receipt',('owned',),None),('lock-a',('a-due',),due)]
    if retry:steps.append(('queue-a',('a-due',),None))
    steps += [('current-a',('a-due',),queued),('lock-b',('b-blocked',),blocked)]
    if not retry:steps.append(('current-b',('b-blocked',),blocked))
    steps += [('lock-c',('c-terminal',),terminal),('save',('owned',payload,result),None)]
    head={'revision':4,'bindings':[{'cache_id':name} for name in ['c-terminal','b-blocked','a-due']]}
    owner,database,pending,events=owner_with_script(steps,head)
    now=datetime(2026,10,21,tzinfo=timezone.utc)
    def clock():
        assert database.active
        events.append('clock')
        return now
    def eligible(cache,when):
        assert database.active and when is now
        events.append('eligible-'+cache['cache_id'])
        return cache['cache_id']=='a-due'
    def generation(connection,cache):
        assert connection is database and database.active and cache is due
        events.append('new-generation')
    def maia(connection,cache):
        assert connection is database and database.active and cache is queued
        events.append('maia-work')
    owner._retry_eligible=eligible
    owner._new_generation=generation
    owner._maia_job=maia
    monkeypatch.setattr(store_module,'utcnow',clock)
    actual=(owner.retry if retry else owner.refresh)('request','owned',4)
    expected=['enter','lock','receipt','head','lock-a']
    expected += ['clock','eligible-a-due','queue-a'] if retry else ['new-generation']
    expected += ['current-a','maia-work','lock-b']
    expected += ['clock','eligible-b-blocked'] if retry else ['current-b']
    expected += ['lock-c','save','commit']
    assert events==expected and not pending
    assert actual==result and not database.active


@pytest.mark.parametrize('case',['replay','conflict','stale'])
def test_command_replay_and_revision_guards_finish_before_cache_work(case):
    payload={'request_id':'request','expected_revision':4,'operation':'retry'}
    retained={'accepted':True,'retained':'original-result'}
    previous=None if case=='stale' else {'payload':{**payload,'operation':'refresh' if case=='conflict' else 'retry'},'result':retained}
    head={'revision':5 if case=='stale' else 4,'bindings':[]}
    owner,database,pending,events=owner_with_script([
        ('lock',('command:owned',),None),('receipt',('owned',),previous)],head)
    if case=='replay':
        assert owner.retry('request','owned',4) is retained
        assert events==['enter','lock','receipt','commit']
    else:
        code='command_replay_conflict' if case=='conflict' else 'request_revision_conflict'
        with pytest.raises(DomainError,match=code):owner.retry('request','owned',4)
        assert events==['enter','lock','receipt']+(['head'] if case=='stale' else [])+['rollback']
    assert not pending and not database.active
