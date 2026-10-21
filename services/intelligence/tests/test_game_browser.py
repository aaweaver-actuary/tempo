"""Read projection fences without optional provider or engine work."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from tempo_intelligence.analysis.games_reader import GamesReader
from tempo_intelligence.errors import DomainError


class Reader(GamesReader):
    def __init__(self):
        self.reads=[]
        @contextmanager
        def transaction(**options):
            self.reads.append(options);yield None
        super().__init__(SimpleNamespace(workspace_id='workspace',transaction=transaction))
        self.revision=1
    def _scope(self,database,run_id):
        return {'run_id':run_id,'revision':self.revision,'input_id':'input','input_revision':1}
    def _input_scope(self,database,scope):
        return {'state':'accepted','game_ids':['c','a','b'],'selections':[]}
    def _game_page(self,database,scope,after,limit):
        return [{'game_id':identifier,'moves':['e2e4']} for identifier in ['a','b','c'] if identifier>after][:limit]


def test_C3_selected_game_pages_are_read_only_complete_and_cursor_bound():
    reader=Reader();first=reader.games('run',limit=2)
    assert [game['game_id'] for game in first['items']]==['a','b']
    assert first['game_count']==3 and first['input_id']=='input'
    assert [game['game_id'] for game in reader.games('run',first['next_after'])['items']]==['c']
    assert all(read=={'read_only':True} for read in reader.reads)
    with pytest.raises(DomainError,match='stale_analysis_game_cursor'):
        reader.games('other-run',first['next_after'])
    reader.revision=2
    with pytest.raises(DomainError,match='stale_analysis_game_cursor'):
        reader.games('run',first['next_after'])


@pytest.mark.parametrize('value',[0,11,True])
def test_C3_game_read_bound_cannot_expand_or_accept_boolean(value):
    with pytest.raises(DomainError,match='invalid_analysis_game_limit'):Reader().games('run',limit=value)


def test_C3_malformed_game_cursor_is_explicit():
    with pytest.raises(DomainError,match='invalid_analysis_game_cursor'):Reader().games('run','bad-cursor')


def test_game_projection_keeps_bounded_reads_in_one_owning_read_transaction():
    run={'run_id':'run','revision':1,'input_id':'input','input_revision':2}
    source={'state':'accepted','game_ids':['a','b'],'selections':[]}
    responses=iter([run,source,[{'payload':{'game_id':'a'}},{'payload':{'game_id':'b'}}]])
    parameters=[]
    transactions=[]
    def execute(_statement,bound=()):
        parameters.append(bound)
        value=next(responses)
        return SimpleNamespace(fetchone=lambda:value,fetchall=lambda:value)
    @contextmanager
    def transaction(**options):
        transactions.append(options)
        yield SimpleNamespace(execute=execute)
    reader=GamesReader(SimpleNamespace(workspace_id='workspace',transaction=transaction))
    result=reader.games('run',limit=1)
    assert result['items']==[{'game_id':'a'}] and result['next_after']
    assert result['game_count']==2 and result['input_revision']==2
    assert parameters==[('run',),('input',2),('input',2,'',2)]
    assert transactions==[{'read_only':True}]


@pytest.mark.parametrize('operation',['scope','scopes'])
def test_game_projection_scope_envelopes_retain_the_owner_read_boundary(operation):
    run={'run_id':'run','revision':1,'input_id':'input','input_revision':2}
    transactions=[]
    parameters=[]
    def execute(_statement,bound=()):
        parameters.append(bound)
        return SimpleNamespace(fetchone=lambda:run,fetchall=lambda:[run])
    @contextmanager
    def transaction(**options):
        transactions.append(options)
        yield SimpleNamespace(execute=execute)
    reader=GamesReader(SimpleNamespace(workspace_id='workspace',transaction=transaction))
    result=reader.scope('run') if operation=='scope' else reader.scopes()
    assert result['workspace_id']=='workspace' and result['schema_version']==1
    if operation=='scope':
        assert result['run_id']=='run'
        assert parameters==[('run',)]
    else:
        assert result['items']==[run]
        assert parameters==[()]
    assert transactions==[{'read_only':True}]
