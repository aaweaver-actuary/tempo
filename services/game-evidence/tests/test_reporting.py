"""Response policy and the existing store publication/read call points."""
from types import SimpleNamespace

import pytest

from tempo_game_evidence.reporting import build_comparison_report
from tempo_game_evidence import comparison, reconciliation
from tempo_game_evidence.source import INDEX_VERSION
from tempo_game_evidence_database.adapters import store as storage


@pytest.mark.parametrize('has_snapshot,serving,source,readiness,reason', [
    (False, False, {}, 'unknown', 'snapshot_missing'),
    (True, True, {}, 'ready', None),
    (True, False, {}, 'unknown', None),
    (True, True, {'source_error': 'invalid_utf8'}, 'unknown', 'invalid_utf8'),
    (False, False, {'source_error': 'invalid_utf8'}, 'unknown', 'invalid_utf8'),
    (True, True, {'source_error': ''}, 'unknown', None),
])
def test_comparison_report_retains_authority_source_precedence_and_fact_identity(
    has_snapshot, serving, source, readiness, reason,
):
    game = {'game_id': 'game', **source}
    findings = [{'status': 'unknown'}]
    snapshot = SimpleNamespace(snapshot_id='snapshot', revision=3,
        publication=SimpleNamespace(publication_id='publication', publication_generation=4, source_revision=5),
        serving_current=lambda: serving) if has_snapshot else None
    result = build_comparison_report(game, 6, snapshot, 'digest', findings)
    assert result == {'schema_version': 1, 'game': game, 'comparison_revision': 6,
        'readiness': readiness, 'reason': reason,
        'snapshot': {'digest': 'digest', 'snapshot_id': 'snapshot', 'revision': 3,
                     'publication_id': 'publication', 'publication_generation': 4,
                     'source_revision': 5} if has_snapshot else None,
        'findings': findings}
    assert result['game'] is game and result['findings'] is findings


@pytest.mark.parametrize('missing', [False, True])
def test_import_status_shapes_or_raises_only_after_read_scope_closes(monkeypatch, missing):
    events = []

    class Timestamp:
        def isoformat(self):
            assert events == ['enter', 'read', 'exit']
            events.append('format')
            return '2026-10-21T00:00:00+00:00'

    result = None if missing else {'import_id': 'import', 'status': 'retrying',
        'reason': 'rate_limited', 'cursor': {'offset': 2}, 'next_attempt_at': Timestamp(), 'attempts': 3}

    class Connection:
        def __enter__(self): events.append('enter'); return self
        def __exit__(self, *_): events.append('exit')
        def execute(self, _query, parameters):
            assert parameters == ('workspace', 'import')
            events.append('read')
            return SimpleNamespace(fetchone=lambda: result)

    store = storage.EvidenceStore('unused')
    monkeypatch.setattr(store, 'connect', Connection)
    if missing:
        with pytest.raises(KeyError) as error:
            store.import_status('workspace', 'import')
        assert error.value.args == ('import',)
        assert events == ['enter', 'read', 'exit']
    else:
        assert store.import_status('workspace', 'import') is result
        assert result == {'import_id': 'import', 'status': 'retrying', 'reason': 'rate_limited',
            'cursor': {'offset': 2}, 'next_attempt_at': '2026-10-21T00:00:00+00:00', 'attempts': 3}
        assert events == ['enter', 'read', 'exit', 'format']


@pytest.mark.parametrize('current,exists', [(True, False), (True, True), (False, False)])
def test_report_construction_stays_after_callback_and_current_locked_read(monkeypatch, current, exists):
    events, reports, writes, json_values = [], [], [], []
    game, findings = {'game_id': 'game', 'moves': []}, []
    row = {'document': game, 'revision': 2, 'input_digest': 'input', 'positions': ['position'],
           'index_receipt': {'input_digest': 'input', 'version': INDEX_VERSION,
                             'position_count': 1, 'complete': True}}
    inputs = {'epoch': 3, 'snapshot_digest': 'digest'}
    snapshot = SimpleNamespace(snapshot_id='snapshot', revision=4, serving_current=lambda: True,
        publication=SimpleNamespace(publication_id='publication', publication_generation=5, source_revision=6))

    class Connection:
        def __init__(self, name, rows): self.name, self.rows, self.count = name, iter(rows), 0
        def __enter__(self): events.append(self.name + '.enter'); return self
        def __exit__(self, *_): events.append(self.name + '.exit')
        def execute(self, _query, parameters):
            self.count += 1
            events.append(self.name + '.' + str(self.count))
            if self.name == 'write': writes.append(parameters)
            value = next(self.rows, None)
            return SimpleNamespace(fetchone=lambda: value)

    connections = iter([Connection('read', [row, inputs, {'document': {}}]),
        Connection('write', [{'epoch': 3}, {'revision': 2, 'report_revision': 6}, {},
                             {'exists': 1} if exists else None])])
    store = storage.EvidenceStore('unused')
    monkeypatch.setattr(store, 'connect', lambda: next(connections))
    monkeypatch.setattr(store, 'comparison_snapshot', lambda *_: events.append('snapshot') or snapshot)
    monkeypatch.setattr(comparison, 'compare_game', lambda *_: events.append('compare') or findings)
    monkeypatch.setattr(store, '_current', lambda *_: events.append('fence') or current)
    monkeypatch.setattr(storage, 'Jsonb', lambda value: json_values.append(value) or value)
    monkeypatch.setattr(reconciliation, 'request_reconciliation', lambda *_: events.append('reconcile'))
    monkeypatch.setattr(reconciliation, 'finalize', lambda *_, **__: events.append('finalize'))

    def construct(*arguments):
        assert events[-1] == 'write.4'
        events.append('report')
        result = build_comparison_report(*arguments)
        reports.append(result)
        return result

    monkeypatch.setattr(storage, 'build_comparison_report', construct)
    store._compare({'game_id': 'game', 'workspace_id': 'workspace', 'job_key': 'job', 'lease_token': 'lease'},
                   lambda: events.append('callback'))
    assert events[:8] == ['read.enter', 'read.1', 'read.2', 'read.3', 'read.exit', 'snapshot', 'compare', 'callback']
    assert events[8:13] == ['write.enter', 'write.1', 'write.2', 'write.3', 'fence']
    if current and not exists:
        assert events[13:] == ['write.4', 'report', 'write.5', 'write.6', 'reconcile', 'finalize', 'write.7', 'write.exit']
        assert writes[4][3] is reports[0]
        assert reports[0]['comparison_revision'] == 7
        assert json_values[0] is reports[0] and json_values[1] is row['positions']
        assert json_values[2] == {'input_digest': 'input', 'version': INDEX_VERSION,
                                  'position_count': 1, 'complete': True}
    else:
        assert not reports
        assert not json_values
        assert events[13:] == (['write.4', 'write.5', 'write.exit'] if current else ['write.4', 'write.exit'])
