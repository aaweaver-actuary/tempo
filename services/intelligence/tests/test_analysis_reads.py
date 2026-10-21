"""Analysis read/workspace/export composition using fixed synthetic rows only."""
import base64
import builtins
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempo_intelligence.analysis import read_work
from tempo_intelligence.analysis.classify import source_findings
from tempo_intelligence.analysis.read_models import Finding
from tempo_intelligence.errors import DomainError
from tempo_intelligence_database.adapters.analysis_store import AnalysisStore


def reader_with_script(steps):
    pending=deque(steps)
    events=[]
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
            assert parameters==expected
            events.append(event)
            return Cursor(row)
    database=Database()
    @contextmanager
    def transaction(**options):
        assert options=={'read_only':True}
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
    class Reader(AnalysisStore):
        def __getattribute__(self,name):
            if name in ('workspace','finding','report'):events.append('lookup-'+name)
            return super().__getattribute__(name)
    reader=Reader(SimpleNamespace(workspace_id='fixture',transaction=transaction))
    return reader,database,pending,events


def run(**changes):
    return {'run_id':'run','revision':2,'published_generation':7,'requested_generation':9,
        'last_complete_generation':6,'state':'partial','payload':{'derivation_version':2,'input_id':'input'},**changes}


def finding():
    directory=Path(__file__).parent.parent/'fixtures/analysis'
    manifest=json.loads((directory/'manifest.json').read_text())
    games=json.loads((directory/'games-0.json').read_text())['games']
    item=source_findings(manifest,games)[0][0]
    item.update(finding_revision=7,evidence_revision='sha256:'+'0'*64,input_id='input',input_revision=1)
    return Finding.model_validate(item).model_dump(mode='json')


@pytest.mark.parametrize('operation',['inputs','input','receipt','engine_result','input_status'])
def test_read_envelopes_and_input_flattening_retain_original_snapshot_return_points(operation):
    source={'payload':{'input_id':'input'},'digest':'sha256:synthetic','state':'accepted'}
    steps={
        'inputs':[('inputs',(),[{'created_at':datetime(2026,10,21,tzinfo=timezone.utc)}])],
        'input':[('input',('input',1),source),('pages',('input',1),[{'payload':{'games':[{'game_id':'z'}]}},{'payload':{'games':[{'game_id':'a'}]}}])],
        'receipt':[('receipt',('command',),{'payload':{'accepted':True},'digest':'sha256:command'})],
        'engine_result':[('result',('result',),{'result':{'synthetic':True},'job':{'generation':1}})],
        'input_status':[('input',('input',1),source),('pages',('input',1),[{'page_id':'p','digest':'sha256:page'}])],
    }
    reader,database,pending,events=reader_with_script(steps[operation])
    if operation=='inputs':
        result=reader.inputs()
        assert result=={'schema_version':1,'workspace_id':'fixture','items':[{'created_at':'2026-10-21T00:00:00+00:00'}]}
    elif operation=='input':
        row,games=reader._input('input',1)
        assert row is source and games==[{'game_id':'z'},{'game_id':'a'}]
    elif operation=='receipt':assert reader.receipt('command')=={'accepted':True,'request_digest':'sha256:command'}
    elif operation=='engine_result':assert reader.engine_result('result')=={'schema_version':1,'workspace_id':'fixture','result_id':'result','result':{'synthetic':True},'job':{'generation':1}}
    else:assert reader.input_status('input',1)=={'schema_version':1,'workspace_id':'fixture','manifest':source['payload'],'digest':source['digest'],'state':'accepted','accepted_pages':[{'page_id':'p','digest':'sha256:page'}]}
    expected=['enter']+[step[0] for step in steps[operation]]+['commit']
    if operation in ('inputs','engine_result','input_status'):expected+=['lookup-workspace']
    assert events==expected and not pending and not database.active


def test_run_list_keeps_count_cursor_iteration_and_status_serialization_on_borrowed_snapshot(monkeypatch):
    first=run();legacy=run(run_id='legacy',payload={'input_id':'old'})
    reader,database,pending,events=reader_with_script([
        ('runs',(),[first,legacy]),('counts',('run',2),[{'state':'complete','count':2},{'state':'pending','count':1}]),
        ('legacy-counts',('legacy',2),[{'state':'complete','count':3}])])
    public=read_work.public
    def serialize(value):
        assert database.active
        events.append('serialize')
        return public(value)
    monkeypatch.setattr(read_work,'public',serialize)
    result=reader.runs()
    assert result['items'][0]['progress']=={'complete':2,'pending':1}
    assert result['items'][0]['scope']=={'input_id':'input'} and not result['items'][0]['engine_scan_complete']
    assert result['items'][1]['recovery_required']=='legacy_analysis_revision_required' and result['items'][1]['engine_scan_complete']
    assert events==['enter','runs','lookup-workspace','counts','iterate','lookup-workspace','serialize','legacy-counts','iterate','lookup-workspace','serialize','commit']
    assert not pending and not database.active


def test_report_history_cursor_bytes_and_late_model_import_follow_read_close(monkeypatch):
    item=finding()
    head=run()
    metadata={'schema_version':1,'workspace_id':'fixture','run_id':'run','revision':1,'generation':7,'derivation_version':2,'ordering':'source-rank-semantic-v2'}
    script=[('head',('run',),head),('history',('run',7),{'revision':1}),('report',('run',1,7),{'payload':metadata}),
        ('items',('run',1,7,-1,'repertoire_miss','repertoire_miss','open','open',2),[{'ordinal':2,'payload':item},{'ordinal':4,'payload':item}])]
    reader,database,pending,events=reader_with_script(script)
    canonical=read_work.canonical_json
    def encode(value):
        assert not database.active
        events.append('encode')
        return canonical(value)
    original_import=builtins.__import__
    def observe_import(name,*arguments,**options):
        if name=='tempo_intelligence.analysis.read_models':
            assert not database.active
            events.append('model-import')
        return original_import(name,*arguments,**options)
    monkeypatch.setattr(read_work,'canonical_json',encode)
    monkeypatch.setattr(builtins,'__import__',observe_import)
    response=AnalysisStore.report(reader,'run',generation=7,limit=1,category='repertoire_miss',dismissal='open')
    scope={'version':1,'workspace':'fixture','run':'run','revision':1,'head_revision':2,'generation':7,'category':'repertoire_miss','dismissal':'open','ordering':'source-rank-semantic-v2'}
    assert response['next_cursor']==base64.urlsafe_b64encode(canonical({**scope,'last':2}).encode()).decode()
    assert response['items']==[item] and response['status']=='stale'
    assert events==['enter','head','history','report','lookup-workspace','items','commit','encode','model-import']
    pending.extend(script[:3]+[('items',('run',1,7,2,'repertoire_miss','repertoire_miss','open','open',2),[{'ordinal':4,'payload':item}])])
    following=AnalysisStore.report(reader,'run',generation=7,limit=1,category='repertoire_miss',dismissal='open',after=response['next_cursor'])
    assert following['items']==[item] and following['next_cursor'] is None
    assert not pending and not database.active


def test_pending_legacy_report_returns_inside_snapshot_without_model_import(monkeypatch):
    reader,database,pending,events=reader_with_script([('head',('run',),run(published_generation=None,payload={})),('previous',('run',2),None)])
    original_import=builtins.__import__
    def observe_import(name,*arguments,**options):
        assert name!='tempo_intelligence.analysis.read_models'
        return original_import(name,*arguments,**options)
    monkeypatch.setattr(builtins,'__import__',observe_import)
    assert AnalysisStore.report(reader,'run')=={'schema_version':1,'workspace_id':'fixture','run_id':'run','status':'pending','items':[],'next_cursor':None,'generation':None,'derivation_version':1,'reasons':['legacy_analysis_revision_required']}
    assert events==['enter','head','previous','lookup-workspace','commit'] and not pending and not database.active


@pytest.mark.parametrize('kind',['invalid','stale'])
def test_report_cursor_rejection_occurs_after_scope_read_before_item_read(kind):
    metadata={'ordering':'source-rank-semantic-v2'}
    reader,database,pending,events=reader_with_script([('head',('run',),run()),('report',('run',2,7),{'payload':metadata})])
    cursor='bad' if kind=='invalid' else base64.urlsafe_b64encode(b'{"last":0,"different":"scope"}').decode()
    with pytest.raises(DomainError,match='invalid_analysis_cursor' if kind=='invalid' else 'stale_analysis_cursor'):
        AnalysisStore.report(reader,'run',after=cursor)
    assert events==['enter','head','report','lookup-workspace','rollback'] and not pending and not database.active


@pytest.mark.parametrize('stale',[False,True],ids=['page','cursor-fence'])
def test_remaining_cursor_retains_lookahead_scope_and_post_read_encoding(stale):
    rows=[{'ordinal':2,'position_id':'a'},{'ordinal':4,'position_id':'b'}]
    steps=[('head',('run',),run())]
    if not stale:steps.append(('positions',('run',2,-1,2),rows))
    reader,database,pending,events=reader_with_script(steps)
    scope={'workspace':'fixture','run':'run','revision':1 if stale else 2,'ordering':'remaining-ordinal-v1'}
    cursor=base64.urlsafe_b64encode(read_work.canonical_json({**scope,'last':2}).encode()).decode()
    if stale:
        with pytest.raises(DomainError,match='stale_remaining_cursor'):reader.remaining('run',cursor,1)
        assert events==['enter','head','lookup-workspace','rollback']
    else:
        result=reader.remaining('run',limit=1)
        assert result['next_after']==cursor and result['items']==rows[:1]
        assert events==['enter','head','lookup-workspace','positions','commit','lookup-workspace']
    assert not pending and not database.active


@pytest.mark.parametrize('visible',[True,False],ids=['accepted-generation','stale-generation'])
def test_finding_fallback_retains_storage_guard_and_post_read_envelope(visible):
    reader,database,pending,events=reader_with_script([('head',('run',),run(published_generation=None)),('previous',('run',2),{'published_generation':7 if visible else 6}),('finding',('run',7,'finding'),{'payload':{'retained':'finding'}})])
    if visible:
        assert AnalysisStore.finding(reader,'run','finding',7)=={'schema_version':1,'workspace_id':'fixture','run_id':'run','generation':7,'finding':{'retained':'finding'}}
        assert events==['enter','head','previous','finding','commit','lookup-workspace']
    else:
        with pytest.raises(DomainError,match='stale_analysis_finding'):AnalysisStore.finding(reader,'run','finding',7)
        assert events==['enter','head','previous','finding','rollback']
    assert not pending and not database.active


@pytest.mark.parametrize('kind',['context','response-proposal'])
def test_export_uses_lazy_finding_report_capabilities_and_preserves_proposal_identity(kind):
    reader,database,pending,events=reader_with_script([])
    proposal={'retained':'proposal'}
    item={'category':'practical_gap','actions':{'response_proposal':{'available':True}},'response_proposal':proposal,'engine':{'omitted':True},'finding_id':'finding'}
    def selected(*args):
        assert args==('run','finding',7)
        events.append('finding-call')
        return {'finding':item}
    def report(*args,**options):
        assert args==('run',) and options=={'generation':7}
        events.append('report-call')
        return {'derivation_version':2}
    reader.finding=selected;reader.report=report
    result=reader.export('run','finding',7,kind)
    if kind=='response-proposal':
        assert result is proposal
        assert events==['lookup-finding','finding-call','lookup-report','report-call']
    else:
        assert result['kind']=='informational_gap_context' and result['finding']=={'category':'practical_gap','finding_id':'finding'}
        assert events==['lookup-finding','finding-call','lookup-workspace']
    assert not pending and not database.active
