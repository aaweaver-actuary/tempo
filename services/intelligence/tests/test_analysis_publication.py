"""Report publication composition against synthetic rows; no database or engine."""
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempo_intelligence.analysis.classify import source_findings
from tempo_intelligence.analysis.read_models import Finding
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import digest
from tempo_intelligence_database.adapters.analysis_store import AnalysisStore


def reader_with_script(steps):
    pending=deque(steps);events=[]
    class Cursor:
        def __init__(self,row):self.row=row
        def fetchone(self):
            assert database.active
            return self.row
        def fetchall(self):
            assert database.active
            return self.row
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
        assert options in ({'read_only':True},{'background':True,'read_only':True},{'background':True})
        phase='capture-read' if options=={'read_only':True} else 'background-read' if options.get('read_only') else 'write'
        events.append(phase);database.active=True
        try:yield database
        except DomainError:
            events.append('rollback');raise
        else:events.append('commit')
        finally:database.active=False
    class Reader(AnalysisStore):
        def __getattribute__(self,name):
            if name in ('_head','_input','capture','_derive_evidence','workspace','derive'):
                events.append('lookup-'+name)
            return super().__getattribute__(name)
    reader=Reader(SimpleNamespace(workspace_id='fixture',transaction=transaction))
    return reader,database,pending,events


def captured():
    directory=Path(__file__).parent.parent/'fixtures/analysis'
    manifest=json.loads((directory/'manifest.json').read_text())
    games=json.loads((directory/'games-0.json').read_text())['games']
    source={'input_id':'input','revision':1,'payload':manifest}
    run={'run_id':'run','revision':2,'requested_generation':9,'published_generation':7,
        'payload':{'derivation_version':2,'input_id':'input','input_revision':1}}
    rows=[{'position_id':'position','state':'complete','error_code':None}]
    return run,rows,{}, {},source,games


def findings(source,games,count=1):
    item=source_findings(source['payload'],games)[0][0]
    item.update(finding_revision=7,evidence_revision='sha256:'+'0'*64,input_id='input',input_revision=1)
    item=Finding.model_validate(item).model_dump(mode='json')
    result=[]
    for ordinal in range(count):
        record=deepcopy(item)
        record.update(finding_id=digest({'finding':ordinal}),supporting_games=['old','new-a','new-b','new-c'])
        result.append(record)
    return result


@pytest.mark.parametrize('with_result',[False,True])
def test_capture_keeps_one_snapshot_cursor_iteration_and_late_input_lookup(with_result):
    run,_,_,_,source,games=captured()
    rows=[{'accepted_root':'result' if with_result else None,'accepted_played':None}]
    dismissed={'finding_id':'finding','revision':3}
    steps=[('head',('run',),run),('positions',('run',2),rows)]
    if with_result:steps.append(('results',(['result'],),[{'result_id':'result','payload':{'wire':True}}]))
    steps.append(('dismissals',(),[dismissed]))
    reader,database,pending,events=reader_with_script(steps)
    def lookup(*args):
        assert args==('input',1) and not database.active
        events.append('input-call')
        return source,games
    reader._input=lookup
    result=reader.capture('run')
    assert result==(run,rows,{'result':{'wire':True}} if with_result else {},{'finding':dismissed},source,games)
    assert events[-3:]==['commit','lookup-_input','input-call']
    assert events.count('capture-read')==1 and events.count('iterate')==(2 if with_result else 1)
    assert not pending and not database.active


def test_current_generation_stops_after_capture_without_evidence_or_callback():
    facts=list(captured());facts[0]['published_generation']=9
    reader,database,pending,events=reader_with_script([])
    reader.capture=lambda run_id:tuple(facts)
    assert reader.derive('run',lambda:pytest.fail('already-current callback')) is False
    assert events==['lookup-derive','lookup-capture']
    assert not pending and not database.active


@pytest.mark.parametrize('outcome',['ordinary','reopen-applied','reopen-raced','head-revision','head-generation','dismissal-revision'])
def test_derivation_keeps_64_item_staging_callback_and_atomic_publication_fences(outcome):
    facts=list(captured());run,rows,results,dismissals,source,games=facts
    items=findings(source,games,65)
    identity=items[0]['finding_id']
    if outcome!='ordinary':dismissals[identity]={'state':'dismissed','revision':8,'supporting_games':['old']}
    written=[];metadata=[]
    def staged(parameters):
        assert parameters[:3]==('run',2,9)
        item=parameters[-1]
        assert parameters[3:5]==(item['finding_id'],len(written))
        assert item['finding_revision']==9 and item['input_id']=='input'
        assert len(item['related_findings'])==64
        written.append(item)
    steps=[('stage-'+str(index),staged,None) for index in range(65)]
    current={**run,'revision':3 if outcome=='head-revision' else 2,'requested_generation':10 if outcome=='head-generation' else 9}
    steps += [('user-lock',(),None),('head',('run',),current)]
    fenced=outcome in ('head-revision','head-generation','dismissal-revision')
    if outcome not in ('head-revision','head-generation'):
        revision_rows=[] if outcome=='ordinary' else [{'finding_id':identity,'revision':10 if outcome=='dismissal-revision' else 8}]
        steps.append(('dismissal-revisions',([item['finding_id'] for item in items],),revision_rows))
        if outcome=='dismissal-revision':steps.append(('request-again',('run',2),None))
        else:
            if outcome in ('reopen-applied','reopen-raced'):
                steps.append(('reopen',(['old','new-a','new-b','new-c'],identity,8),{'finding_id':identity} if outcome=='reopen-applied' else None))
                if outcome=='reopen-applied':steps.append(('siblings',('run',identity),None))
            def report(parameters):
                assert parameters[:3]==('run',2,9)
                metadata.append(parameters[3])
            steps += [('report',report,None),('publish',(9,True,9,'complete','run',2),None)]
    reader,database,pending,events=reader_with_script(steps)
    def capture(run_id):
        assert run_id=='run' and not database.active
        events.append('capture-call')
        return tuple(facts)
    def evidence(*args):
        assert args==(run,rows,results,source,games) and not database.active
        events.append('evidence-call')
        return items,[],set()
    def callback():
        assert not database.active and events.count('commit')==2
        events.append('before-publish')
    reader.capture=capture;reader._derive_evidence=evidence
    assert reader.derive('run',callback) is not fenced
    assert len(written)==65 and written[0]['dismissal_state']=='open'
    assert written[0]['evidence_revision']!=('sha256:'+'0'*64)
    assert events.count('write')==3 and events.count('commit')==3
    assert events.index('evidence-call')<events.index('write')<events.index('stage-64')<events.index('lookup-workspace')<events.index('before-publish')<events.index('user-lock')
    if not fenced:
        assert metadata[0]['complete'] is True and metadata[0]['finding_count']==65 and metadata[0]['generation']==9
        assert metadata[0]['ordering']=='source-rank-semantic-v2'
        assert events.index('report')<events.index('publish')
    assert not pending and not database.active


@pytest.mark.parametrize('present',[False,True])
def test_legacy_derivation_reads_prior_generation_and_keeps_original_metadata_and_evidence(present):
    facts=list(captured());run,_,_,_,source,games=facts;run['payload']['derivation_version']=1
    item=findings(source,games)[0]
    retained={'complete':False,'unknown_coverage':[{'reason':'retained'}],'historical':'preserved'}
    saved=[]
    steps=[('legacy-report',('run',2,7),{'payload':retained} if present else None)]
    if present:
        steps += [('legacy-findings',('run',2,7),[{'payload':item}]),
            ('stage',lambda args:saved.append(args[-1]),None),('user-lock',(),None),('head',('run',),run),
            ('dismissal-revisions',([item['finding_id']],),[]),
            ('report',lambda args:saved.append(args[-1]),None),('publish',(9,False,9,'partial','run',2),None)]
    reader,database,pending,events=reader_with_script(steps)
    reader.capture=lambda run_id:tuple(facts)
    if not present:
        with pytest.raises(DomainError,match='legacy_analysis_revision_required'):reader.derive('run')
        assert events[-1]=='rollback' and 'write' not in events
    else:
        assert reader.derive('run') is True
        assert saved[0]['evidence_revision']=='sha256:'+'0'*64
        assert saved[1]=={**retained,'generation':9,'derivation_version':1,'ordering':'category-source-rank-semantic-v1'}
        assert events.index('background-read')<events.index('legacy-findings')<events.index('commit')<events.index('write')
    assert 'lookup-_derive_evidence' not in events and not pending and not database.active


@pytest.mark.parametrize('pending_run',[False,True])
def test_next_derivation_closes_background_read_before_lazy_derive_lookup(pending_run):
    reader,database,pending,events=reader_with_script([('next',(),{'run_id':'run'} if pending_run else None)])
    def derive(run_id):
        assert run_id=='run' and not database.active
        events.append('derive-call')
        return 'derived'
    reader.derive=derive
    assert reader.derive_next()==('derived' if pending_run else False)
    assert events==['background-read','next','commit']+(['lookup-derive','derive-call'] if pending_run else [])
    assert not pending and not database.active
