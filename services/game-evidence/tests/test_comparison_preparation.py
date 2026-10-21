"""Existing comparison/index/list policy stays outside persistence adapters."""
from types import SimpleNamespace

import pytest

from tempo_game_evidence import comparison, reconciliation
from tempo_game_evidence.source import INDEX_VERSION, identity
from tempo_game_evidence_database.adapters.store import EvidenceStore


@pytest.mark.parametrize('fault', [None, 'input_digest', 'version', 'position_count', 'complete',
                                 'positions_missing', 'positions_short', 'source_error', 'snapshot_missing'])
def test_comparison_preparation_retains_reuse_replay_snapshot_token_callback_order(monkeypatch, fault):
    events = []
    game = {'moves': ['e2e4']}
    positions = [{'cached': 1}, {'cached': 2}]
    replayed, findings, snapshot = [{'replayed': 1}, {'replayed': 2}], [{'status': 'matched'}], object()
    receipt = {'input_digest': 'input', 'version': INDEX_VERSION, 'position_count': 2, 'complete': True}
    row = {'document': game, 'revision': 3, 'input_digest': 'input', 'positions': positions, 'index_receipt': receipt}
    inputs = {'epoch': 4, 'snapshot_digest': 'digest'}
    snapshot_row = None if fault == 'snapshot_missing' else {'document': {'snapshot': 1}}
    if fault in receipt:
        receipt[fault] = False if fault == 'complete' else 'changed'
    elif fault == 'positions_missing': row['positions'] = None
    elif fault == 'positions_short': row['positions'] = []
    elif fault == 'source_error':
        game['source_error'] = 'invalid_utf8'
        row['index_receipt'] = None

    def replay(value):
        assert value is game
        events.append('replay')
        return replayed

    class Store:
        @property
        def comparison_snapshot(self):
            events.append('snapshot.lookup')
            def load(digest, document):
                assert digest == 'digest' and document is snapshot_row['document']
                events.append('snapshot.load')
                return snapshot
            return load

    def compare(value, indexed, authority):
        assert value is game and authority is snapshot
        events.append('compare')
        return findings

    def token(*parts):
        events.append('token')
        return identity(*parts)

    monkeypatch.setattr(comparison, 'replay_positions', replay)
    monkeypatch.setattr(comparison, 'compare_game', compare)
    monkeypatch.setattr(comparison, 'identity', token)
    result = comparison.prepare_game_comparison(row, inputs, snapshot_row, Store(), lambda: events.append('callback'))
    invalid_index = fault in {'input_digest', 'version', 'position_count', 'complete', 'positions_missing', 'positions_short'}
    expected_positions = [] if fault == 'source_error' else replayed if invalid_index else positions
    assert result[0] is game
    if fault == 'source_error':
        assert result[1] == []
    else:
        assert result[1] is expected_positions
    assert result[2] is (None if fault == 'snapshot_missing' else snapshot)
    assert result[3] == ([] if fault in {'source_error', 'snapshot_missing'} else findings)
    assert result[4] == identity(3, 'input', 4, 'digest', INDEX_VERSION)
    assert events == (['replay'] if invalid_index else []) + ([] if fault == 'snapshot_missing' else ['snapshot.lookup', 'snapshot.load']) + ([] if fault in {'source_error', 'snapshot_missing'} else ['compare']) + ['token', 'callback']


@pytest.mark.parametrize('source_error', [False, True])
def test_index_receipt_retains_source_completeness_and_position_facts(source_error):
    game = {'source_error': 'invalid_utf8'} if source_error else {}
    assert comparison.comparison_index_receipt({'input_digest': 'input'}, game, [1, 2]) == {
        'input_digest': 'input', 'version': INDEX_VERSION, 'position_count': 2, 'complete': not source_error}


def test_reconciliation_pair_retains_borrowed_transaction_and_loader_lookup_order(monkeypatch):
    database, loader, events = object(), object(), []

    class Store:
        @property
        def comparison_snapshot(self):
            events.append('loader.lookup')
            return loader

    def request(value, workspace, game_id):
        assert value is database and (workspace, game_id) == ('workspace', 'game')
        events.append('request')

    def finalize(value, game_id, *, snapshot_loader):
        assert value is database and game_id == 'game' and snapshot_loader is loader
        events.append('finalize')

    monkeypatch.setattr(reconciliation, 'request_reconciliation', request)
    monkeypatch.setattr(reconciliation, 'finalize', finalize)
    reconciliation.reconcile_comparison(database, {'workspace_id': 'workspace'}, {'game_id': 'game'}, Store())
    assert events == ['request', 'loader.lookup', 'finalize']


@pytest.mark.parametrize('limit', [0, 101, 1, 100])
def test_game_page_limit_retains_validation_before_connection_and_bound_read(monkeypatch, limit):
    events, result = [], [{'game_id': 'game'}]

    class Connection:
        def __enter__(self): events.append('enter'); return self
        def __exit__(self, *_): events.append('exit')
        def execute(self, _query, parameters):
            assert parameters == ('workspace', 'cursor', limit)
            events.append('read')
            return SimpleNamespace(fetchall=lambda: result)

    store = EvidenceStore('unused')
    monkeypatch.setattr(store, 'connect', Connection)
    if limit in {0, 101}:
        with pytest.raises(ValueError, match='^page_limit_out_of_range$'):
            store.list_games('workspace', 'cursor', limit)
        assert events == []
    else:
        assert store.list_games('workspace', 'cursor', limit) is result
        assert events == ['enter', 'read', 'exit']
