"""Pure import/report decisions preserve the store's existing call ordering."""
from types import SimpleNamespace

import pytest

from tempo_game_evidence import source
from tempo_game_evidence.reporting import comparison_report_response
from tempo_game_evidence_database.adapters.store import EvidenceStore


def test_import_replay_returns_before_normalization_or_writer(monkeypatch):
    raw = b'unchanged source'
    game_id, _raw_digest, input_digest = source.prepare_import_identity('w', 'pgn', 'one', 'Player', raw)
    events = []

    class Reader:
        def __enter__(self): events.append('read-enter'); return self
        def __exit__(self, *_): events.append('read-exit')
        def execute(self, _query, parameters):
            assert parameters == (game_id,)
            events.append('read')
            return SimpleNamespace(fetchone=lambda: {'input_digest': input_digest})

    def forbidden(*_arguments, **_keywords):
        raise AssertionError('Replay performed normalization or writing')

    store = EvidenceStore('unused')
    monkeypatch.setattr(store, 'connect', Reader)
    monkeypatch.setattr(store, '_import', forbidden)
    monkeypatch.setattr(source, 'normalize_pgn', forbidden)
    assert store.import_pgn('w', 'pgn', 'one', 'Player', raw) == game_id
    assert events == ['read-enter', 'read', 'read-exit']


def test_import_normalization_stays_between_reader_close_and_writer_open(monkeypatch):
    events = []
    raw = b'new source'
    expected_id, expected_digest, _ = source.prepare_import_identity('w', 'pgn', 'one', 'Player', raw)

    class Connection:
        def __enter__(self): events.append('enter'); return self
        def __exit__(self, *_): events.append('exit')
        def execute(self, *_):
            events.append('read')
            return SimpleNamespace(fetchone=lambda: None)

    def normalize(value, player, *, played_at):
        assert value == raw and player == 'Player' and played_at is None
        assert events == ['enter', 'read', 'exit']
        events.append('normalize')
        return {'moves': []}

    def persist(_database, *_arguments):
        document = _arguments[-1]
        assert events == ['enter', 'read', 'exit', 'normalize', 'enter']
        assert document == {'moves': [], 'workspace_id': 'w', 'game_id': expected_id,
                            'provider': 'pgn', 'provider_game_id': 'one', 'source_digest': expected_digest}
        events.append('persist')
        return expected_id

    store = EvidenceStore('unused')
    monkeypatch.setattr(store, 'connect', Connection)
    monkeypatch.setattr(store, '_import', persist)
    monkeypatch.setattr(source, 'normalize_pgn', normalize)
    assert store.import_pgn('w', 'pgn', 'one', 'Player', raw) == expected_id
    assert events == ['enter', 'read', 'exit', 'normalize', 'enter', 'persist', 'exit']


def test_import_identity_guards_precede_hashing_and_normalization():
    class Oversized:
        def __len__(self): return source.MAX_SOURCE_BYTES + 1

    with pytest.raises(ValueError, match='^source_byte_bound_exceeded$'):
        source.prepare_import_identity('w', 'pgn', 'one', 'Player', Oversized())
    with pytest.raises(ValueError, match='^explicit_source_identity_required$'):
        source.prepare_import_identity('w', 'pgn', 'one', ' ', b'source')


def test_import_error_document_and_source_facts_are_preserved(monkeypatch):
    def invalid(*_arguments, **_keywords):
        raise ValueError('synthetic invalid PGN')
    monkeypatch.setattr(source, 'normalize_pgn', invalid)
    assert source.prepare_import_document('w', 'pgn', 'one', 'PLAYER', b'raw', 'digest', 'game') == {
        'source_error': 'synthetic invalid PGN', 'player': 'player', 'played_at': None,
        'workspace_id': 'w', 'game_id': 'game', 'provider': 'pgn', 'provider_game_id': 'one',
        'source_digest': 'digest',
    }


@pytest.mark.parametrize('report_revision,document_revision,snapshot,current_snapshot,expected_reason', [
    (1, 1, 'current', 'current', None),
    (1, 2, 'current', 'current', 'source_revision_changed'),
    (1, 1, 'old', 'current', 'snapshot_changed'),
    (1, 2, 'old', 'current', 'source_revision_changed'),
])
def test_report_readiness_precedence_and_existing_report_identity(
    report_revision, document_revision, snapshot, current_snapshot, expected_reason,
):
    report = {'schema_version': 1, 'game': {'revision': report_revision},
              'readiness': 'ready', 'findings': [], 'snapshot': {'digest': snapshot}}
    delivery = {'status': 'completed'}
    row = {'report': report, 'document': {'revision': document_revision},
           'current_snapshot_digest': current_snapshot}
    result = comparison_report_response(row, delivery)
    assert result is report and result['delivery'] is delivery
    assert result['readiness'] == ('pending' if expected_reason else 'ready')
    assert result.get('reason') == expected_reason


def test_missing_report_retains_original_pending_response():
    document = {'revision': 1}
    delivery = {'status': 'not_ready'}
    assert comparison_report_response({'report': None, 'document': document,
                                       'current_snapshot_digest': 'current'}, delivery) == {
        'schema_version': 1, 'game': document, 'readiness': 'pending', 'findings': [],
        'delivery': delivery, 'reason': 'snapshot_changed',
    }
