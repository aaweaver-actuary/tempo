"""Read projection fences without optional provider or engine work."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from tempo_intelligence.analysis.store import AnalysisStore
from tempo_intelligence.errors import DomainError


class Reader(AnalysisStore):
    def __init__(self):
        self.reads=[]
        @contextmanager
        def transaction(**options):
            self.reads.append(options);yield None
        super().__init__(SimpleNamespace(workspace_id='workspace',transaction=transaction))
        self.revision=1
    def _head(self,db,run_id,lock=False):
        assert not lock
        return {'revision':self.revision,'payload':{'input_id':'input','input_revision':1}}
    def _input(self,input_id,revision):
        return {'state':'accepted','input_id':input_id,'revision':revision,'payload':{'selections':[]}},[{'game_id':identifier,'moves':['e2e4']} for identifier in ['c','a','b']]


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
