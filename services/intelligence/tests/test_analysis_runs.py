"""Run/receipt/command/dismissal composition with synthetic fixed-row responses."""
from collections import deque
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tempo_intelligence.analysis import run_work
from tempo_intelligence.analysis.models import Command, RunRequest, payload_digest
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import digest
from tempo_intelligence_database.adapters.analysis_store import AnalysisStore


def reader_with_script(steps):
    pending=deque(steps);events=[];written=[]
    class Cursor:
        def __init__(self,row):self.row=row
        def fetchone(self):return self.row
        def fetchall(self):return self.row
        def __iter__(self):
            assert database.active
            events.append('iterate')
            return iter(self.row)
    class Database:
        active=False
        def execute(self,_query,parameters=()):
            assert self.active
            event,expected,row=pending.popleft()
            actual=tuple(getattr(item,'obj',item) for item in parameters)
            if callable(expected):expected(actual)
            else:assert actual==expected
            events.append(event)
            return Cursor(row)
    database=Database()
    @contextmanager
    def transaction(**options):
        assert options in ({},{'read_only':True})
        events.append('read' if options else 'write');database.active=True
        try:yield database
        except DomainError:
            events.append('rollback');raise
        else:events.append('commit')
        finally:database.active=False
    class Reader(AnalysisStore):
        def __getattribute__(self,name):
            if name in ('_workspace','_input','evidence_records','workspace','_receipt','_save_receipt','_head','_status','finding'):
                events.append('lookup-'+name)
            return super().__getattribute__(name)
    reader=Reader(SimpleNamespace(workspace_id='fixture',transaction=transaction))
    class Observed:
        def __init__(self,value):self.value=value
        def __getattr__(self,name):
            if name=='run_id':events.append('value-run_id')
            return getattr(self.value,name)
        def model_dump(self,**options):
            events.append(('dump',database.active))
            return self.value.model_dump(**options)
    def receipt(identity,payload,**fields):
        def check(parameters):
            assert parameters[:2]==(identity,digest(payload))
            result=parameters[2]
            assert result['request_digest']==digest(payload)
            for key,value in fields.items():assert result[key]==value
            written.append(result)
        return check
    return reader,database,pending,events,written,Observed,receipt


def run(**changes):
    return {'run_id':'run','revision':2,'state':'pending','payload':{'derivation_version':2},'budget':10,
        'requested_generation':5,'published_generation':7,'last_complete_generation':3,**changes}


@pytest.mark.parametrize('case',['promote','newer-head','immutable-conflict'])
def test_run_admission_retains_generation_hash_points_64_item_staging_and_head_publication(monkeypatch,case):
    value=RunRequest(schema_version=1,workspace_id='fixture',run_id='run',revision=2,expected_revision=1,input_id='input',input_revision=1,position_budget=1)
    payload={**value.model_dump(mode='json'),'derivation_version':2}
    positions=[{'game_id':'game','source_revision':1,'ply':index} for index in range(65)]
    steps=[('input',('input',1),{'state':'accepted'}),('pages',('input',1),[]),
        ('lock',('analysis:run',),None),('old',('run',2),{'digest':'different','state':'staging'} if case=='immutable-conflict' else None),('head',('run',),{'revision':1})]
    if case!='immutable-conflict':
        steps += [('previous',('run',1),{'requested_generation':4,'last_complete_generation':3}),('create',('run',2,payload,payload_digest(value),1,5,3),None)]
        for ordinal,position in enumerate(positions):
            identity=digest({'game':'game','revision':1,'ply':ordinal})
            stored={**position,'position_id':identity,'workspace_id':'fixture'}
            steps.append(('position-'+str(ordinal),('run',2,ordinal,identity,stored,'pending' if ordinal==0 else 'unbudgeted'),None))
        steps += [('lock-final',('analysis:run',),None),('head-final',('run',),{'revision':3 if case=='newer-head' else 2})]
        if case=='promote':steps += [('promote',('run',2),None),('publish-head',('run',2),None)]
        final=run(revision=3 if case=='newer-head' else 2)
        steps += [('status-head',('run',),final),('counts',('run',final['revision']),[{'state':'pending','count':1},{'state':'unbudgeted','count':64}])]
    reader,database,pending,events,_,Observed,_=reader_with_script(steps)
    def ordered(games):
        assert games==[] and not database.active
        events.append('positions')
        return positions
    monkeypatch.setattr(run_work,'ordered_positions',ordered)
    if case=='immutable-conflict':
        with pytest.raises(DomainError,match='analysis_run_conflict'):reader.submit(Observed(value))
        assert events[-1]=='rollback' and events.count('write')==1
    else:
        result=reader.submit(Observed(value))
        assert result['revision']==final['revision'] and result['progress']=={'pending':1,'unbudgeted':64}
        assert events.count('read')==1 and events.count('write')==4
        assert events[-8:]==['lookup-_status','lookup-_head','value-run_id','status-head','counts','iterate','lookup-workspace','commit']
        assert [position['workspace_id'] for position in positions]==['fixture']*65
    assert [event for event in events if isinstance(event,tuple)]==[('dump',False),('dump',True)]
    assert not pending and not database.active


@pytest.mark.parametrize('action',['continue','retry','resume','cancel'])
def test_run_commands_keep_action_effect_order_borrowed_status_and_receipt_mutation(action):
    command=Command(command_id='owned',action=action,expected_revision=2,budget=3)
    payload={'run_id':'run',**command.model_dump(mode='json')}
    current=run()
    steps=[('head',('run',),current),('receipt-lock',('analysis-command:owned',),None),('receipt',('owned',),None)]
    if action=='continue':steps += [('budget',(3,'run',2),None),('unbudgeted',('run',2,13),None)]
    if action=='retry':steps.append(('quality',('run',2),None))
    if action in ('retry','resume'):steps.append(('failed',('run',2),None))
    if action!='cancel':steps.append(('queue',('run',2),None))
    else:steps += [('reap',('run',2),None),('request-cancel',('run',2),None),('pending',('run',2),None),('running',('run',2),None),('finish',('run',2),None)]
    steps += [('status-head',('run',),current),('counts',('run',2),[{'state':'complete','count':1}])]
    reader,database,pending,events,written,Observed,receipt=reader_with_script(steps)
    pending.append(('save',receipt('owned',payload,accepted=True,command_id='owned'),None))
    result=reader.command('run',Observed(command))
    assert result is written[0] and result['request_digest']==digest(payload)
    assert events[0]==('dump',False) and events.count('write')==1
    assert events.index('lookup-_status')<events.index('status-head')<events.index('counts')<events.index('lookup-_save_receipt')<events.index('save')
    assert events[-1]=='commit' and not pending and not database.active


@pytest.mark.parametrize('conflict',[False,True],ids=['replay-before-stale','receipt-conflict'])
def test_run_command_receipt_guard_and_replay_finish_before_revision_or_effect_work(conflict):
    command=Command(command_id='owned',action='retry',expected_revision=2)
    payload={'run_id':'run',**command.model_dump(mode='json')}
    retained={'payload':{'retained':'reply'},'digest':'different' if conflict else digest(payload)}
    reader,database,pending,events,_,_,_=reader_with_script([
        ('head',('run',),run(revision=3)),('lock',('analysis-command:owned',),None),('receipt',('owned',),retained)])
    if conflict:
        with pytest.raises(DomainError,match='analysis_command_conflict'):reader.command('run',command)
        assert events[-1]=='rollback'
    else:
        assert reader.command('run',command)=={'retained':'reply','request_digest':digest(payload)}
        assert events[-1]=='commit'
    assert events.count('write')==1 and 'lookup-_status' not in events and not pending and not database.active


@pytest.mark.parametrize('case',['write','first-replay','second-replay','stale'])
def test_dismissal_keeps_two_phase_receipts_finding_lookup_user_lock_guard_and_receipt_last(case):
    command=Command(command_id='owned',action='dismiss',expected_revision=7)
    payload={'run_id':'run','finding_id':'finding','generation':7,**command.model_dump(mode='json')}
    retained={'payload':{'retained':'reply'},'digest':digest(payload)}
    steps=[('lock-first',('analysis-command:owned',),None),('receipt-first',('owned',),retained if case=='first-replay' else None)]
    if case!='first-replay':
        steps += [('user-lock',(),None),('head',('run',),run(published_generation=8 if case in ('stale','second-replay') else 7)),
            ('lock-second',('analysis-command:owned',),None),('receipt-second',('owned',),retained if case=='second-replay' else None)]
        if case=='write':steps += [('dismiss',('finding','dismissed',['game']),None),('invalidate',('run','finding'),None)]
    reader,database,pending,events,written,Observed,receipt=reader_with_script(steps)
    def selected(*args):
        assert args==('run','finding',7) and not database.active
        events.append('finding-call')
        return {'finding':{'finding_revision':7,'supporting_games':['game']}}
    reader.finding=selected
    if case=='write':pending.append(('save',receipt('owned',payload,dismissal_state='dismissed',publication_pending=True),None))
    if case=='stale':
        with pytest.raises(DomainError,match='stale_analysis_finding'):reader.dismiss('run','finding',7,Observed(command))
        assert events[-1]=='rollback'
    else:
        result=reader.dismiss('run','finding',7,Observed(command))
        assert result==(written[0] if case=='write' else {'retained':'reply','request_digest':digest(payload)})
        assert events[-1]=='commit'
    assert [event for event in events if isinstance(event,tuple)]==[('dump',False)]
    assert events.count('write')==(1 if case=='first-replay' else 2)
    if case!='first-replay':assert events.index('commit')<events.index('lookup-finding')<events.index('finding-call')<events.index('user-lock')
    if case=='write':assert events.index('dismiss')<events.index('invalidate')<events.index('lookup-workspace')<events.index('lookup-_save_receipt')<events.index('save')
    assert not pending and not database.active
