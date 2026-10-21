"""Import metadata and fixed retry values retain existing persistence points."""
from datetime import timedelta
import struct
from types import SimpleNamespace

import pytest
from psycopg.adapt import PyFormat, Transformer

from tempo_game_evidence import providers, source
from tempo_game_evidence_database.adapters import store as storage


@pytest.mark.parametrize('path,revision', [('insert', 1), ('update', 5), ('replay', None)])
def test_import_metadata_clock_player_and_json_adaptation_keep_write_order(monkeypatch, path, revision):
    events, parameters = [], []
    document = {'moves': []}
    previous = None if path == 'insert' else {'input_digest': 'input' if path == 'replay' else 'old', 'revision': 4}

    class Timestamp:
        def isoformat(self):
            assert document['revision'] == revision
            events.append('format')
            return '2026-10-21T00:00:00+00:00'

    def clock():
        events.append('clock')
        return Timestamp()

    class Player(str):
        def casefold(self):
            events.append('player')
            return super().casefold()

    class Connection:
        def execute(self, _query, values):
            parameters.append(values)
            events.append('query:' + str(len(parameters)))
            return SimpleNamespace(fetchone=lambda: previous)

    def json(value):
        assert value is document
        events.append('json')
        return value

    store = storage.EvidenceStore('unused')
    monkeypatch.setattr(source, 'utc_now', clock)
    monkeypatch.setattr(storage, 'Jsonb', json)
    monkeypatch.setattr(store, '_request', lambda *_: events.append('request'))
    assert store._import(Connection(), 'workspace', 'pgn', 'source', Player('PLAYER'), b'raw',
                         'raw-digest', 'input', 'game', document) == 'game'
    if path == 'replay':
        assert events == ['query:1', 'query:2', 'query:3']
        assert document == {'moves': []}
    else:
        assert events == ['query:1', 'query:2', 'query:3', 'clock', 'format', 'player',
                          'json', 'query:4', 'json', 'query:5', 'request']
        assert parameters[3] == ('game', 'workspace', 'pgn', 'source', 'player', revision,
                                 'input', 'raw-digest', b'raw', document)
        assert parameters[4] == ('game', revision, 'input', 'raw-digest', b'raw', document)
        assert document == {'moves': [], 'revision': revision, 'imported_at': '2026-10-21T00:00:00+00:00'}


def test_provider_unavailable_retry_keeps_sixty_second_server_interval_and_release_order(monkeypatch):
    events, calls = [], []
    row = {'import_id': 'import', 'cursor': {}, 'configuration': {
        'workspace_id': 'workspace', 'provider': 'lichess', 'account': 'Player',
        'since': '2026-10-01T00:00:00Z', 'until': '2026-10-02T00:00:00Z'}}

    class Connection:
        def __init__(self, index): self.index = index
        def __enter__(self): events.append(('enter', self.index)); return self
        def __exit__(self, *_): events.append(('exit', self.index))
        def execute(self, _query, parameters):
            events.append(('write', self.index))
            calls.append((self.index, parameters))
            if self.index == 3:
                assert "next_attempt_at=now()+interval '60 seconds'" in _query

    connections = iter(Connection(index) for index in range(1, 5))
    store = SimpleNamespace(connect=lambda: next(connections))
    monkeypatch.setattr(providers.uuid, 'uuid4', lambda: SimpleNamespace(hex='token'))
    monkeypatch.setattr(providers.storage, 'claimable_import', lambda _: row)
    monkeypatch.setattr(providers.storage, 'acquire_provider_lease', lambda *_: True)
    monkeypatch.setattr(providers.storage, 'begin_import_attempt', lambda *_: events.append(('attempt', 1)))

    def unavailable(*_):
        events.append(('unavailable', 2))
        raise ValueError('synthetic unavailable source')

    monkeypatch.setattr(providers.storage, 'saved_import_page', unavailable)
    assert providers.import_one(store, client=object()) is True
    assert events == [('enter', 1), ('attempt', 1), ('exit', 1), ('enter', 2), ('unavailable', 2),
                      ('exit', 2), ('enter', 3), ('write', 3), ('exit', 3),
                      ('enter', 4), ('write', 4), ('write', 4), ('exit', 4)]
    assert calls == [(3, ('import', 'token')),
                     (4, ('import', 'token')), (4, ('lichess', 'token'))]


@pytest.mark.parametrize('seconds', [5, 60])
def test_fixed_retry_timedeltas_adapt_offline_to_same_postgres_interval(seconds):
    value = timedelta(seconds=seconds)
    dumper = Transformer().get_dumper(value, PyFormat.BINARY)
    assert dumper.oid == 1186
    assert struct.unpack('!qii', dumper.dump(value)) == (seconds * 1_000_000, 0, 0)
