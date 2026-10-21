"""Snapshot preparation keeps replay, stale rejection and locked-read timing."""
from types import SimpleNamespace

import pytest

from tempo_game_evidence import snapshot as application
from tempo_game_evidence.source import identity
from tempo_game_evidence_database.adapters import store as storage


def model(events, label, *, schema=2, revision=2, generation=2, snapshot_id='snapshot'):
    def dump(*, mode):
        assert mode == 'json'
        events.append('dump:' + label)
        return {'source': label}

    def routes():
        events.append('routes:' + label)
        return [(None, [label, 'shared'], None)]

    return SimpleNamespace(schema_version=schema, snapshot_id=snapshot_id, revision=revision,
        publication=SimpleNamespace(workspace_id='workspace', publication_id='publication',
                                    publication_generation=generation),
        model_dump=dump, compiled_routes=routes)


@pytest.mark.parametrize('schema,has_previous,parses', [(1, True, 1), (2, True, 2), (2, False, 0)])
def test_affected_positions_retain_prior_parse_count_and_route_order(monkeypatch, schema, has_previous, parses):
    events = []
    current = {'changed_positions': ['retained', 'shared']}
    incoming = model(events, 'new', schema=schema)
    previous = {'document': {'source': 'prior'}} if has_previous else None

    def parse(document):
        assert document is previous['document']
        events.append('parse:prior')
        return model(events, 'old', revision=1, generation=1)

    monkeypatch.setattr(application, 'parse_snapshot', parse)
    affected = application.snapshot_changed_positions(incoming, previous, current)
    assert affected == ({'retained', 'shared', 'new', 'old'} if has_previous else {'retained', 'shared', 'new'})
    assert current == {'changed_positions': ['retained', 'shared']}
    assert events == ['parse:prior'] * parses + ['routes:new'] + (['routes:old'] if has_previous else [])


@pytest.mark.parametrize('fault,reason', [('revision', 'stale_snapshot_revision'), ('generation', 'stale_snapshot_generation')])
def test_stale_snapshot_rejection_precedes_changed_positions_and_compilation(monkeypatch, fault, reason):
    events = []
    incoming = model(events, 'new')
    prior = model(events, 'old', revision=2 if fault == 'revision' else 1,
                  generation=3 if fault == 'generation' else 1)
    monkeypatch.setattr(application, 'parse_snapshot', lambda _: events.append('parse:prior') or prior)
    with pytest.raises(ValueError, match='^' + reason + '$'):
        application.snapshot_changed_positions(incoming, {'document': {}}, {})
    assert events == ['parse:prior']


def test_invalid_snapshot_is_rejected_before_opening_a_connection(monkeypatch):
    store = storage.EvidenceStore('unused')

    def forbidden():
        raise AssertionError('invalid input acquired a connection')

    monkeypatch.setattr(store, 'connect', forbidden)
    with pytest.raises(ValueError, match='^unsupported_snapshot_version$'):
        store.load_snapshot({'schema_version': 999})


@pytest.mark.parametrize('path', ['install', 'replay', 'conflict', 'stale'])
def test_snapshot_preparation_and_changed_positions_keep_original_read_write_points(monkeypatch, path):
    events, parameters = [], []
    incoming = model(events, 'new')
    prior = model(events, 'old', revision=2 if path == 'stale' else 1, generation=1)
    document = {'source': 'new'}
    digest = identity(document)

    def parse(value):
        events.append('parse:' + value['source'])
        return incoming if value['source'] == 'new' else prior

    current = {'snapshot_digest': digest if path == 'replay' else 'prior-digest',
               'changed_positions': ['retained']}
    rows = iter([None, current, {'digest': 'conflict'} if path == 'conflict' else None,
                 {'document': {'source': 'prior'}}])

    class Connection:
        def __enter__(self): events.append('enter'); return self
        def __exit__(self, *_): events.append('exit')
        def execute(self, _query, values):
            parameters.append(values)
            events.append('query:' + str(len(parameters)))
            result = next(rows, None)
            return SimpleNamespace(fetchone=lambda: result)

    store = storage.EvidenceStore('unused')
    monkeypatch.setattr(application, 'parse_snapshot', parse)
    monkeypatch.setattr(store, 'connect', Connection)
    monkeypatch.setattr(storage, 'Jsonb', lambda value: value)
    if path in {'conflict', 'stale'}:
        reason = 'immutable_snapshot_revision_conflict' if path == 'conflict' else 'stale_snapshot_revision'
        with pytest.raises(ValueError, match='^' + reason + '$'):
            store.load_snapshot({'source': 'new'})
    else:
        assert store.load_snapshot({'source': 'new'}) == digest
    assert events[:5] == ['parse:new', 'dump:new', 'enter', 'query:1', 'query:2']
    if path == 'install':
        assert events[5:] == ['query:3', 'query:4', 'parse:prior', 'parse:prior',
                             'routes:new', 'routes:old', 'query:5', 'query:6', 'query:7', 'exit']
        assert parameters[4] == (digest, 'workspace', 'snapshot', 2, document)
        assert parameters[5] == (digest, ['new', 'old', 'retained', 'shared'], 'workspace')
        assert parameters[6] == ('rescope:workspace', 'workspace')
    elif path == 'replay':
        assert events[5:] == ['exit']
    elif path == 'conflict':
        assert events[5:] == ['query:3', 'exit']
    else:
        assert events[5:] == ['query:3', 'query:4', 'parse:prior', 'exit']
