"""Durable study preferences, separate from probability and production admission (#157)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import database, main


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'learning-priorities.db')
    database.initialize()
    with database.connection() as connection:
        for policy_id in ('italian', 'ruy', '__retained_cards__'):
            connection.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)',
                               (policy_id, policy_id, policy_id + '.pgn', date.today().isoformat()))
    from app.services.database_executor import database_writer
    database_writer.start()
    try:
        yield TestClient(main.app)
    finally:
        database_writer.stop()


def test_learning_priority_defaults_are_neutral_and_exclude_system_policies(workspace):
    response = workspace.get('/api/settings/repertoire-learning-priorities')
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot['schema_version'] == 1
    assert snapshot['policies'] == [
        {'policy_id': policy_id, 'learning_priority_weight': 1.0,
         'learning_priority_active': True, 'revision': 1}
        for policy_id in ('italian', 'ruy')]
    assert len(snapshot['snapshot_revision']) == 64


def save(workspace, policy_id='italian', weight=2, active=True, revision=1):
    return workspace.put(f'/api/repertoires/{policy_id}/learning-priority', json={
        'learning_priority_weight': weight, 'learning_priority_active': active,
        'expected_revision': revision})


def test_learning_priority_zero_fractional_inactive_and_reactivation_preserve_preferences(workspace):
    assert save(workspace, weight=0).json()['learning_priority_weight'] == 0
    inactive = save(workspace, weight=1.5, active=False, revision=2).json()
    assert inactive == {'policy_id': 'italian', 'learning_priority_weight': 1.5,
                        'learning_priority_active': False, 'revision': 3}
    assert workspace.get('/api/settings/repertoire-learning-priorities').json()['policies'][0] == inactive
    restored = save(workspace, weight=1.5, active=True, revision=3).json()
    assert restored['learning_priority_weight'] == 1.5
    assert restored['revision'] == 4


@pytest.mark.parametrize('weight', [-1, True, False, '2', None, {}, []])
def test_learning_priority_invalid_weights_are_rejected_without_mutation(workspace, weight):
    before = workspace.get('/api/settings/repertoire-learning-priorities').json()
    assert save(workspace, weight=weight).status_code == 422
    assert workspace.get('/api/settings/repertoire-learning-priorities').json() == before


@pytest.mark.parametrize('weight', [float('nan'), float('inf'), float('-inf'), 10 ** 400])
def test_learning_priority_nonfinite_weights_are_rejected(weight):
    from pydantic import ValidationError
    from app.learning_priorities import LearningPriorityUpdate
    with pytest.raises(ValidationError):
        LearningPriorityUpdate(learning_priority_weight=weight, learning_priority_active=True, expected_revision=1)


@pytest.mark.parametrize('changes', [
    {'expected_revision': None}, {'expected_revision': True}, {'expected_revision': 0},
    {'expected_revision': '1'}, {'learning_priority_active': 1},
    {'learning_priority_active': 'true'}, {'surprise': 2},
])
def test_learning_priority_strict_contract_rejects_invalid_flags_revisions_and_unknown_fields(workspace, changes):
    payload = {'learning_priority_weight': 1, 'learning_priority_active': True, 'expected_revision': 1}
    assert workspace.put('/api/repertoires/italian/learning-priority', json={**payload, **changes}).status_code == 422


@pytest.mark.parametrize('missing', ['learning_priority_weight', 'learning_priority_active', 'expected_revision'])
def test_learning_priority_missing_update_fields_do_not_reset_preferences(workspace, missing):
    assert save(workspace).status_code == 200
    payload = {'learning_priority_weight': 1, 'learning_priority_active': True, 'expected_revision': 2}
    del payload[missing]
    assert workspace.put('/api/repertoires/italian/learning-priority', json=payload).status_code == 422
    assert workspace.get('/api/settings/repertoire-learning-priorities').json()['policies'][0]['learning_priority_weight'] == 2


@pytest.mark.parametrize('policy_id', ['missing', '__retained_cards__', '__tactics__'])
def test_learning_priority_missing_and_system_policy_updates_return_not_found(workspace, policy_id):
    assert save(workspace, policy_id=policy_id).status_code == 404


def test_learning_priority_stale_writes_conflict_independent_policies_and_noops_do_not(workspace):
    baseline = workspace.get('/api/settings/repertoire-learning-priorities').json()
    assert save(workspace, weight=1).json()['revision'] == 1
    assert workspace.get('/api/settings/repertoire-learning-priorities').json() == baseline
    assert save(workspace, weight=2).json()['revision'] == 2
    for weight in (2, 3):
        stale = save(workspace, weight=weight)
        assert stale.status_code == 409
        assert stale.json()['detail']['current_revision'] == 2
    assert save(workspace, policy_id='ruy', weight=3).json()['revision'] == 2


def test_learning_priority_snapshot_is_immutable_deterministic_and_rename_independent(workspace):
    from dataclasses import FrozenInstanceError
    from app.learning_priorities import read_learning_priority_snapshot
    with database.read_connection() as connection:
        captured = read_learning_priority_snapshot(connection)
    with pytest.raises(FrozenInstanceError):
        captured.policies[0].learning_priority_weight = 9
    detached = captured.document()
    detached['policies'][0]['learning_priority_weight'] = 9
    assert captured.policies[0].learning_priority_weight == 1
    assert workspace.patch('/api/repertoires/italian', json={'name': 'Renamed'}).status_code == 200
    assert workspace.get('/api/settings/repertoire-learning-priorities').json() == captured.document()
    assert save(workspace).status_code == 200
    assert captured.policies[0].revision == 1
    assert workspace.get('/api/settings/repertoire-learning-priorities').json()['snapshot_revision'] != captured.snapshot_revision


def test_learning_priority_reimport_color_identity_restart_and_ordinary_settings_preserve_weights(workspace):
    pgn = '[Event "Priority"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 *'
    def admit(color):
        result = workspace.post('/api/imports/pgn', files={'file': ('priority.pgn', pgn, 'application/x-chess-pgn')},
                                data={'trained_color': color, 'depth': 6})
        assert result.status_code == 200, result.text
        return result.json()['repertoire_id']
    white_id = admit('white')
    black_id = admit('black')
    assert white_id != black_id
    assert save(workspace, white_id, weight=2.5, active=False).status_code == 200
    assert workspace.patch(f'/api/repertoires/{white_id}', json={'name': 'Renamed import'}).status_code == 200
    assert admit('white') == white_id
    assert workspace.put(f'/api/repertoires/{white_id}/settings', json={'new_cards_per_day': 7}).status_code == 200
    before_restart = workspace.get('/api/settings/repertoire-learning-priorities').json()
    database.initialize()
    assert workspace.get('/api/settings/repertoire-learning-priorities').json() == before_restart
    entries = {entry['policy_id']: entry for entry in before_restart['policies']}
    assert entries[white_id]['learning_priority_weight'] == 2.5
    assert entries[white_id]['learning_priority_active'] is False
    assert entries[white_id]['revision'] == 2
    assert entries[black_id]['learning_priority_weight'] == 1


def test_learning_priority_deleted_identity_is_absent_and_new_import_is_neutral(workspace):
    pgn = '[Event "Priority"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 *'
    def admit():
        response = workspace.post('/api/imports/pgn', files={'file': ('deleted.pgn', pgn)},
                                  data={'trained_color': 'white', 'depth': 6})
        assert response.status_code == 200, response.text
        return response.json()['repertoire_id']
    old_id = admit()
    assert save(workspace, old_id, weight=9).status_code == 200
    assert workspace.delete(f'/api/repertoires/{old_id}').status_code == 200
    assert save(workspace, old_id, revision=2).status_code == 404
    new_id = admit()
    assert new_id != old_id
    entries = {entry['policy_id']: entry for entry in workspace.get('/api/settings/repertoire-learning-priorities').json()['policies']}
    assert old_id not in entries
    assert entries[new_id]['learning_priority_weight'] == 1


def test_learning_priority_italian_ruy_scale_and_shared_card_preferences_preserve_conditional_models(workspace):
    from test_opening_policy_reach import ITALIAN, RUY, evaluate, scores, target
    results = {policy_id: evaluate(policy_id, moves) for policy_id, moves in [('italian', ITALIAN), ('ruy', RUY)]}
    baseline_scores = {policy_id: scores(result, target(result, moves[:-1]))
                       for policy_id, result, moves in [('italian', results['italian'], ITALIAN), ('ruy', results['ruy'], RUY)]}
    def preferences():
        return {entry['policy_id']: entry for entry in workspace.get('/api/settings/repertoire-learning-priorities').json()['policies']}
    def study_scores(values):
        # Test-local contract example only: weight final study benefit, never reach.
        return {policy_id: preference['learning_priority_weight'] * values[policy_id]
                if preference['learning_priority_active'] else 0
                for policy_id, preference in preferences().items()}
    assert save(workspace, weight=1).status_code == 200
    assert save(workspace, 'ruy', weight=2).status_code == 200
    initial = study_scores({'italian': 1, 'ruy': 1})
    assert initial == {'italian': 1, 'ruy': 2}
    assert save(workspace, weight=10).status_code == 200
    assert save(workspace, 'ruy', weight=20, revision=2).status_code == 200
    scaled = study_scores({'italian': 1, 'ruy': 1})
    assert sorted(initial, key=initial.get) == sorted(scaled, key=scaled.get)
    assert save(workspace, weight=0, revision=2).status_code == 200
    shared_contributions = study_scores({'italian': 0.5, 'ruy': 0.5})
    assert shared_contributions == {'italian': 0, 'ruy': 10}
    assert sum(shared_contributions.values()) > 0
    assert save(workspace, 'ruy', weight=20, active=False, revision=3).status_code == 200
    assert sum(study_scores({'italian': 0.5, 'ruy': 0.5}).values()) == 0
    for policy_id, moves in [('italian', ITALIAN), ('ruy', RUY)]:
        current = evaluate(policy_id, moves)
        assert current == results[policy_id]
        assert scores(current, target(current, moves[:-1])) == baseline_scores[policy_id]


def test_learning_priority_changes_leave_admissions_fsrs_graph_evidence_tasks_and_queue_unchanged(workspace):
    import chess
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at) "
                           "VALUES('shared','italian','prefix',?,'[\"e2e4\"]','learning','2026-10-11','{}','2026-10-10')", (chess.STARTING_FEN,))
        for policy_id in ('italian', 'ruy'):
            connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,'shared')", (policy_id,))
        connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('shared','correct','2026-10-10',0,7)")
        connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_repertoire_id,admission_kind) VALUES('2026-10-11','shared',3,'italian','review')")
        connection.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES('italian',1,'2026-10-10')")
    def unchanged_state():
        with database.read_connection() as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='repertoires' ORDER BY name")]
            return {table: sorted(tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')) for table in tables}
    before = unchanged_state()
    assert save(workspace, weight=0).status_code == 200
    assert save(workspace, 'ruy', weight=2, active=False).status_code == 200
    assert unchanged_state() == before


def test_learning_priority_postgres_api_dispatch_preserves_version_and_idempotency(workspace, monkeypatch):
    from app import command_dispatch
    captured = []
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    def dispatch(command, payload, **options):
        captured.append((command, payload, options))
        return {'policy_id': 'italian', 'learning_priority_weight': 2.5,
                'learning_priority_active': False, 'revision': 2}
    monkeypatch.setattr(command_dispatch, 'dispatch_command', dispatch)
    from app.learning_priorities import LearningPriorityUpdate
    response = main.put_repertoire_learning_priority('italian', LearningPriorityUpdate(
        learning_priority_weight=2.5, learning_priority_active=False, expected_revision=1), 'priority-save')
    assert response['revision'] == 2
    assert captured == [('repertoires.learning_priority.update', {'policy_id': 'italian', 'request': {
        'learning_priority_weight': 2.5, 'learning_priority_active': False, 'expected_revision': 1}},
        {'idempotency_key': 'priority-save'})]


def test_learning_priority_empty_workspace_snapshot_is_stable(workspace):
    with database.connection() as connection:
        connection.execute("DELETE FROM repertoires WHERE id IN ('italian','ruy')")
    first = workspace.get('/api/settings/repertoire-learning-priorities').json()
    assert first['policies'] == []
    assert first == workspace.get('/api/settings/repertoire-learning-priorities').json()


def test_learning_priority_integer_float_and_negative_zero_noops_are_canonical(workspace):
    baseline = workspace.get('/api/settings/repertoire-learning-priorities').json()
    assert save(workspace, weight=1.0).json()['revision'] == 1
    assert workspace.get('/api/settings/repertoire-learning-priorities').json() == baseline
    assert save(workspace, weight=-0.0).json()['learning_priority_weight'] == 0.0
    zero_snapshot = workspace.get('/api/settings/repertoire-learning-priorities').json()
    assert save(workspace, weight=0, revision=2).json()['revision'] == 2
    assert workspace.get('/api/settings/repertoire-learning-priorities').json() == zero_snapshot


@pytest.mark.parametrize('invalid', ['NaN', 'Infinity', '-Infinity', '1e400'])
@pytest.mark.parametrize('field', ['learning_priority_weight', 'learning_priority_active', 'expected_revision', 'unknown'])
def test_learning_priority_nonfinite_http_inputs_return_422_without_mutation(workspace, field, invalid):
    import json
    client = TestClient(main.app, raise_server_exceptions=False)
    baseline = client.get('/api/settings/repertoire-learning-priorities').json()
    payload = {'learning_priority_weight': 1, 'learning_priority_active': True, 'expected_revision': 1}
    serialized = json.dumps(payload)[:-1] + ',"' + field + '":' + invalid + '}'
    response = client.put('/api/repertoires/italian/learning-priority', content=serialized,
                          headers={'Content-Type': 'application/json'})
    assert response.status_code == 422, response.text
    assert response.json()['detail']
    assert client.get('/api/settings/repertoire-learning-priorities').json() == baseline
