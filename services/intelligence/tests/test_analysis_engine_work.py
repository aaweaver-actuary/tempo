"""Durable engine-work composition with pure wire models and synthetic rows only."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import chess
import pytest

from tempo_intelligence.analysis import engine_work
from tempo_intelligence.analysis.engine import make_job, validate_result
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import digest
from tempo_intelligence_database.adapters import analysis_engine
from tempo_intelligence_database.adapters.analysis_store import AnalysisStore

POLICY=engine_work
CLOCK_OWNER=analysis_engine
NOW=datetime(2026,10,21,tzinfo=timezone.utc)


def reader_with_script(steps,monkeypatch):
    pending=deque(steps);events=[]
    class Cursor:
        def __init__(self,row):self.row=row
        def fetchone(self):
            assert database.active
            return self.row
        def fetchall(self):
            assert database.active
            return self.row
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
        assert options=={'background':True}
        events.append('write');database.active=True
        try:yield database
        except (DomainError,ValueError):
            events.append('rollback');raise
        else:events.append('commit')
        finally:database.active=False
    class Reader(AnalysisStore):
        def __getattribute__(self,name):
            if name in ('_head','workspace'):events.append('lookup-'+name)
            return super().__getattribute__(name)
    class Clock:
        @staticmethod
        def now(zone):
            assert database.active and zone is timezone.utc
            events.append('clock')
            return NOW
    monkeypatch.setattr(CLOCK_OWNER,'datetime',Clock)
    reader=Reader(SimpleNamespace(workspace_id='fixture',transaction=transaction))
    return reader,database,pending,events


def facts(stage='root'):
    run={'run_id':'run','revision':2,'state':'pending','payload':{'derivation_version':2}}
    position={'workspace_id':'fixture','position_id':'position','starting_fen':chess.STARTING_FEN,'history':[],'actual_move':'e2e4'}
    row={'run_id':'run','revision':2,'position_id':'position','state':'running','stage':stage,'payload':position,
        'execution_generation':3,'accepted_root':'root-result' if stage=='played' else None,
        'lease_token':'lease','leased_until':NOW+timedelta(seconds=30),'attempts':1}
    claim={**row,'job':builder(position,'run',2,3,stage,20)}
    return run,row,claim


def builder(position,run_id,revision,generation,stage,depth):
    job=make_job(position,run_id,revision,generation,stage,'synthetic-test-profile',1024,depth)
    job['input_digest']=digest({key:value for key,value in job.items() if key not in ('input_digest','execution_generation')})
    return job


def result(claim,move=None):
    job=claim['job']
    return {'contract_version':1,**{key:job[key] for key in ('workspace_id','job_id','request_revision','execution_generation','worker','engine_version')},
        'provenance':{'input_digest':job['input_digest'],'runtime_name':'synthetic','runtime_version':'test','implementation_version':'test',
            'started_at':'2026-10-16T00:00:00Z','finished_at':'2026-10-16T00:00:01Z'},
        'variations':[{'rank':1,'moves':[move or claim['payload']['actual_move']],
            'score':{'kind':'centipawns','value':0},'score_perspective':'side_to_move','depth':20}]}


@pytest.mark.parametrize('case',['lease','job-mismatch','capacity-race','capacity-busy','cancelled','incompatible'])
def test_claim_keeps_recovery_capacity_recheck_pure_builder_validation_and_lease_order(monkeypatch,case):
    run,row,_=facts('played' if case=='incompatible' else 'root')
    steps=[('head',('run',),run)]
    if case=='cancelled':
        run['state']='cancel_requested'
        steps += [('reap',('run',2),None),('running',('run',2),None),('cancelled',('run',2),None)]
    else:
        steps += [('eligible',('run',2),row),('capacity-first',(),{'running':True} if case=='capacity-busy' else None)]
        if case!='capacity-busy':steps += [('capacity-lock',(),None),('capacity-second',(),{'running':True} if case=='capacity-race' else None)]
        if case=='incompatible':
            prior=result({**row,'job':builder(row['payload'],'run',2,3,'root',20)})
            steps += [('root',('root-result',),{'payload':prior}),('incompatible',('run',2,'position'),None),('request-again',('run',2),None)]
    reader,database,pending,events=reader_with_script(steps,monkeypatch)
    def build(*args):
        assert database.active and args==(row['payload'],'run',2,4,'root',20)
        events.append('build')
        job=builder(*args)
        if case=='job-mismatch':job['workspace_id']='other'
        return job
    def token():
        assert database.active
        events.append('token')
        return SimpleNamespace(hex='new-lease')
    monkeypatch.setattr(engine_work.uuid,'uuid4',token)
    if case=='lease':
        job=builder(row['payload'],'run',2,4,'root',20)
        pending.extend([('execution',(job['job_id'],4,'run',2,'position','root',job),None),('lease',(4,'new-lease','run',2,'position'),None)])
    if case=='job-mismatch':
        with pytest.raises(DomainError,match='analysis_job_input_mismatch'):reader.claim('run',build)
        assert events[-1]=='rollback' and 'token' not in events
    else:
        output=reader.claim('run',build,{'engine_version':'different'} if case=='incompatible' else None)
        assert output==({**row,'execution_generation':4,'lease_token':'new-lease','job':job} if case=='lease' else {'error':'incompatible_engine_profile'} if case=='incompatible' else None)
        assert events[-1]=='commit'
    if case in ('lease','job-mismatch'):assert events.index('capacity-second')<events.index('build')<events.index('lookup-workspace')
    else:assert 'build' not in events and 'token' not in events
    if case=='lease':assert events.index('lookup-workspace')<events.index('token')<events.index('execution')<events.index('lease')
    assert events.count('write')==1 and not pending and not database.active


@pytest.mark.parametrize('case',['root-complete','root-played','played','replay','replay-conflict','expired','cancelled','execution-mismatch'])
def test_acceptance_keeps_prewrite_validation_replay_clock_fences_and_stage_write_order(monkeypatch,case):
    run,row,claim=facts('played' if case=='played' else 'root')
    wire=validate_result(claim['job'],result(claim,'d2d4' if case=='root-played' else None));identity=digest(wire)
    if case=='expired':row['leased_until']=NOW
    if case=='cancelled':run['state']='cancel_requested'
    prior={'payload':{} if case=='replay-conflict' else wire,'result_id':'old-result'} if case in ('replay','replay-conflict') else None
    steps=[('head',('run',),run),('old',(wire['job_id'],3),prior)]
    if not prior:
        steps.append(('position',('run',2,'position'),row))
        if case not in ('expired','cancelled'):
            steps.append(('execution',(wire['job_id'],3),{'payload':{} if case=='execution-mismatch' else claim['job']}))
            if case!='execution-mismatch':
                steps.append(('result',(identity,wire['job_id'],3,wire),None))
                if case=='played':
                    root_job=builder(row['payload'],'run',2,2,'root',20)
                    root=result({**claim,'job':root_job},'d2d4')
                    steps += [('root',('root-result',),{'payload':root}),('root-job',('root-result',),{'payload':root_job}),
                        ('played',(identity,None,'run',2,'position'),None)]
                else:steps.append(('root-update',(identity,'pending' if case=='root-played' else 'complete','played' if case=='root-played' else 'root',None,'run',2,'position'),None))
                steps += [('recovery',('run',2),None),('generation',('run',2),None)]
    reader,database,pending,events=reader_with_script(steps,monkeypatch)
    real_validate=POLICY.validate_result
    def validate(*args):
        assert not database.active
        events.append('validate')
        return real_validate(*args)
    monkeypatch.setattr(POLICY,'validate_result',validate)
    errors={'replay-conflict':'analysis_result_replay_conflict','expired':'stale_analysis_execution',
        'cancelled':'stale_analysis_execution','execution-mismatch':'analysis_execution_mismatch'}
    if case in errors:
        with pytest.raises(DomainError,match=errors[case]):reader.accept(claim,wire)
        assert events[-1]=='rollback'
    else:
        assert reader.accept(claim,wire)=={'result_id':'old-result' if case=='replay' else identity,'replayed':case=='replay'}
        assert events[-1]=='commit'
    assert events[0:3]==['validate','write','lookup-_head']
    assert events.count('clock')==(0 if case in ('replay','replay-conflict','cancelled') else 1)
    if case not in ('replay','replay-conflict','expired','cancelled','execution-mismatch'):
        assert events.index('clock')<events.index('execution')<events.index('result')<events.index('recovery')<events.index('generation')
    assert not pending and not database.active


@pytest.mark.parametrize('case',['retry','deferred','exhausted','coerced-code','cancelled','status','status-mismatch','stale'])
def test_failure_keeps_fence_before_status_event_then_backoff_and_cancel_settlement(monkeypatch,case):
    run,row,claim=facts()
    if case=='exhausted':row['attempts']=2
    if case=='cancelled':run['state']='cancel_requested'
    if case=='stale':row['lease_token']='new-owner'
    code='unknown-private-detail' if case=='coerced-code' else 'worker_session_lost'
    normalized='execution_failed' if case=='coerced-code' else code
    status={'contract_version':1,'workspace_id':'other' if case=='status-mismatch' else 'fixture','job_id':claim['job']['job_id'],
        'request_revision':2,'execution_generation':3,'status':'failed','retryable':True,'reason':'worker_session_lost'} if case in ('status','status-mismatch','stale') else None
    event={'model':'JobStatus','payload':status} if status is not None else {'model':'IntelligenceExecutionFailure','reason':normalized}
    steps=[('head',('run',),run),('position',('run',2,'position'),row)]
    if case not in ('stale','status-mismatch'):
        attempts=row['attempts']+(0 if case=='deferred' else 1)
        state='cancelled' if case=='cancelled' else 'failed' if case=='exhausted' else 'retry_wait'
        identity=digest({'job':claim['job']['job_id'],'generation':3,'event':event})
        steps += [('event',(identity,claim['job']['job_id'],3,event),None),
            ('failure',(state,attempts,normalized,min(60,2**attempts),'run',2,'position'),None),('generation',('run',2),None)]
        if case=='cancelled':steps += [('running',('run',2),None),('cancelled',('run',2),None)]
    reader,database,pending,events=reader_with_script(steps,monkeypatch)
    if case=='status-mismatch':
        with pytest.raises(DomainError,match='analysis_status_identity_mismatch'):reader.failure(claim,code=code,status_payload=status)
        assert events[-1]=='rollback'
    else:
        assert reader.failure(claim,code=code,deferred=case=='deferred',status_payload=status) is (case!='stale')
        assert events[-1]=='commit'
    if case in ('status','status-mismatch'):assert events.index('position')<events.index('lookup-workspace')
    if case=='stale':assert 'lookup-workspace' not in events and 'event' not in events
    if case not in ('status-mismatch','stale'):assert events.index('event')<events.index('failure')<events.index('generation')
    assert events.count('write')==1 and not pending and not database.active


@pytest.mark.parametrize('changed',[False,True])
def test_unavailable_marks_only_fixed_pending_rows_and_requests_generation_on_change(monkeypatch,changed):
    run,_,_=facts()
    steps=[('head',('run',),run),('mark',('run',2),[{'position_id':'position'}] if changed else [])]
    if changed:steps.append(('generation',('run',2),None))
    reader,database,pending,events=reader_with_script(steps,monkeypatch)
    assert reader.mark_engine_unavailable('run') is None
    assert events==['write','lookup-_head','head','mark']+(['generation'] if changed else [])+['commit']
    assert not pending and not database.active
