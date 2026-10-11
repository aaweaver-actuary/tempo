"""Engine selection reserves ordinary games without losing interactive demand."""
from datetime import datetime, timezone
import json

import pytest

from app import database, main
from app.services import threat_pipeline
from test_defensive_analysis_pause import seed_exercise_requests


@pytest.fixture
def engine_store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'engine-fairness.sqlite')
    database.initialize()
    seed_exercise_requests()
    with database.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
        connection.execute('INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)',
                           ('lichess:pause-test', datetime.now(timezone.utc).isoformat()))


def release_automated(claimed):
    with database.connection() as connection:
        connection.execute("UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,lease_expires_at=NULL WHERE id=?",
                           (claimed['id'],))


def test_engine_reserves_ordinary_game_after_three_automated_selections(engine_store):
    for _ in range(3):
        selected = threat_pipeline.claim_analysis_request()
        assert selected
        release_automated(selected)
    assert threat_pipeline.claim_analysis_request() is None
    ordinary = main._claim_game_analysis()['job']
    assert ordinary['game_id'] == 'lichess:pause-test'
    assert threat_pipeline.claim_analysis_request() is not None


def test_engine_turn_survives_restart_and_skips_ineligible_or_paused_games(engine_store):
    for _ in range(3):
        release_automated(threat_pipeline.claim_analysis_request())
    database.initialize()
    assert threat_pipeline.claim_analysis_request() is None
    with database.connection() as connection:
        connection.execute("INSERT INTO background_activity(source,work_id,paused,updated_at) VALUES('game_analysis','lichess:pause-test',1,?)",
                           (datetime.now(timezone.utc).isoformat(),))
    release_automated(threat_pipeline.claim_analysis_request())
    with database.connection() as connection:
        connection.execute('UPDATE background_activity SET paused=0')
        connection.execute('UPDATE imported_games SET rated=0')
    assert threat_pipeline.claim_analysis_request() is not None


def test_engine_interactive_attempt_does_not_consume_reserved_ordinary_turn(engine_store):
    for _ in range(3):
        release_automated(threat_pipeline.claim_analysis_request())
    with database.connection() as connection:
        relation = connection.execute('SELECT candidate_id,request_id FROM threat_candidate_requests LIMIT 1').fetchone()
        connection.execute("INSERT INTO threat_candidate_requests(candidate_id,request_id,role) VALUES(?,?,'attempt')",
                           tuple(relation))
    selected = threat_pipeline.claim_analysis_request()
    assert selected['id'] == relation['request_id']
    with database.read_connection() as connection:
        assert connection.execute('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 3


def test_engine_transaction_rollback_preserves_lease_and_streak(engine_store, monkeypatch):
    from app.services import engine_scheduling
    original = engine_scheduling.record_automated_selection

    def interrupt_after_transition(connection):
        original(connection)
        raise RuntimeError('Injected before commit')

    monkeypatch.setattr(engine_scheduling, 'record_automated_selection', interrupt_after_transition)
    with pytest.raises(RuntimeError, match='Injected before commit'):
        threat_pipeline.claim_analysis_request()
    with database.read_connection() as connection:
        assert connection.execute('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 0
        assert all(tuple(row) == ('queued', 0) for row in connection.execute(
            'SELECT state,attempts FROM threat_analysis_requests'))


def set_streak(value):
    with database.connection() as connection:
        connection.execute('UPDATE engine_scheduling_state SET automated_streak=?', (value,))


def read_streak():
    with database.read_connection() as connection:
        return connection.execute('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0]


def test_engine_no_ordinary_work_keeps_automated_claims_available(engine_store):
    set_streak(3)
    with database.connection() as connection:
        connection.execute('DELETE FROM game_analysis_jobs')
    assert threat_pipeline.claim_analysis_request()
    assert read_streak() == 3


def test_engine_paused_ordinary_work_does_not_block_automated_claim(engine_store):
    set_streak(3)
    with database.connection() as connection:
        connection.execute("INSERT INTO background_activity(source,work_id,paused,updated_at) VALUES('game_analysis','lichess:pause-test',1,?)",
                           (datetime.now(timezone.utc).isoformat(),))
    assert threat_pipeline.claim_analysis_request()
    assert read_streak() == 3


def test_engine_deleted_ordinary_game_does_not_block_recommendation(engine_store):
    from test_defensive_analysis_pause import attach_recommendation
    with database.read_connection() as connection:
        request_id = connection.execute('SELECT id FROM threat_analysis_requests LIMIT 1').fetchone()[0]
    attach_recommendation(request_id, coverage=True)
    set_streak(3)
    with database.connection() as connection:
        connection.execute("DELETE FROM imported_games WHERE id='lichess:pause-test'")
    assert threat_pipeline.claim_analysis_request()
    assert read_streak() == 3


def test_engine_unexpired_ordinary_lease_does_not_block_automated_claim(engine_store):
    set_streak(3)
    with database.connection() as connection:
        connection.execute("UPDATE game_analysis_jobs SET status='leased',lease_expires_at='2099-01-01'")
    assert threat_pipeline.claim_analysis_request()
    assert read_streak() == 3


def test_engine_ineligible_ordinary_game_does_not_block_automated_claim(engine_store):
    set_streak(3)
    with database.connection() as connection:
        connection.execute('UPDATE imported_games SET rated=0')
    assert threat_pipeline.claim_analysis_request()
    assert read_streak() == 3


def test_engine_failed_automated_lease_update_does_not_increment_streak(engine_store):
    with database.connection() as connection:
        connection.execute("CREATE TRIGGER reject_engine_claim BEFORE UPDATE ON threat_analysis_requests "
                           "WHEN NEW.state='leased' BEGIN SELECT RAISE(IGNORE); END")
    assert threat_pipeline.claim_analysis_request() is None
    assert read_streak() == 0


def test_engine_interactive_demand_prevents_new_ordinary_claim(engine_store):
    set_streak(3)
    with database.connection() as connection:
        candidate_id, request_id = connection.execute(
            'SELECT candidate_id,request_id FROM threat_candidate_requests LIMIT 1').fetchone()
        connection.execute("INSERT INTO threat_candidate_requests VALUES(?,?,'attempt')", (candidate_id, request_id))
    assert main._claim_game_analysis() == {'job': None}
    assert read_streak() == 3
    assert threat_pipeline.claim_analysis_request()['id'] == request_id
    assert main._claim_game_analysis()['job']
    assert read_streak() == 0


def test_engine_stale_preferred_game_does_not_spend_reserved_turn(engine_store):
    set_streak(3)
    assert main._claim_game_analysis('deleted-game') == {'job': None}
    assert read_streak() == 3
    assert main._claim_game_analysis('lichess:pause-test')['job']
    assert read_streak() == 0


def test_engine_empty_automated_queue_does_not_increment_streak(engine_store):
    with database.connection() as connection:
        connection.execute("UPDATE threat_analysis_requests SET state='complete'")
    assert threat_pipeline.claim_analysis_request() is None
    assert read_streak() == 0


def test_engine_manual_promotion_counts_as_automated_selection(engine_store):
    with database.connection() as connection:
        request_id = connection.execute('SELECT id FROM threat_analysis_requests LIMIT 1').fetchone()[0]
        connection.execute("INSERT INTO background_activity(source,work_id,promoted,updated_at) VALUES('threat_analysis',?,1,?)",
                           (request_id, datetime.now(timezone.utc).isoformat()))
    for expected_streak in (1, 2, 3):
        selected = threat_pipeline.claim_analysis_request()
        assert selected['id'] == request_id
        assert read_streak() == expected_streak
        release_automated(selected)
    assert threat_pipeline.claim_analysis_request() is None


def test_engine_accepted_ordinary_claim_resets_streak(engine_store):
    set_streak(3)
    assert main._claim_game_analysis()['job']
    assert read_streak() == 0


def test_engine_ordinary_claim_rollback_does_not_spend_reserved_turn(engine_store, monkeypatch):
    from app.services import engine_scheduling
    set_streak(3)
    original = engine_scheduling.record_ordinary_selection

    def interrupt_after_transition(connection):
        original(connection)
        raise RuntimeError('Injected ordinary rollback')

    monkeypatch.setattr(engine_scheduling, 'record_ordinary_selection', interrupt_after_transition)
    with pytest.raises(RuntimeError, match='ordinary rollback'):
        main._claim_game_analysis()
    assert read_streak() == 3
    with database.read_connection() as connection:
        assert connection.execute('SELECT status,attempts FROM game_analysis_jobs').fetchone()[:] == ('queued', 0)


def test_engine_restart_preserves_reserved_turn(engine_store):
    set_streak(3)
    database.initialize()
    assert read_streak() == 3
    assert threat_pipeline.claim_analysis_request() is None


def test_engine_foreground_contention_yields_without_spending_streak(engine_store):
    from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
    set_streak(3)
    with activity_gate.foreground(), activity_gate.background_request():
        with pytest.raises(BackgroundAdmissionDeferred):
            threat_pipeline.claim_analysis_request()
    assert read_streak() == 3


def test_engine_missing_scheduling_state_reports_actionable_error(engine_store):
    from fastapi import HTTPException
    with database.connection() as connection:
        connection.execute('DELETE FROM engine_scheduling_state')
    with pytest.raises(HTTPException) as error:
        threat_pipeline.claim_analysis_request()
    assert error.value.status_code == 503
    assert 'restore the database' in error.value.detail


@pytest.mark.parametrize('receipt_state', ['complete', 'executing', 'retrying', 'blocked'])
def test_engine_position_journal_replays_receipt_before_preparing_stale_hint(engine_store, monkeypatch, receipt_state):
    from app import game_analysis_commands
    from app.models import GameAnalysisPositionClaimRequest
    saved = {'job': {'game_id': 'already-leased', 'lease_id': 'original-lease', 'kind': 'search'}}
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(main, 'read_operation', lambda operation_id, **options:
        {'state': receipt_state, 'response': saved})
    monkeypatch.setattr(game_analysis_commands, 'prepare_position_claim',
        lambda *_args: pytest.fail('Receipt replay prepared new work'))
    result = main.claim_game_analysis_position(GameAnalysisPositionClaimRequest(preferred_game_id='deleted-game'),
                                              engine_worker='docker', idempotency_key='original-operation')
    if receipt_state == 'complete':
        assert result == saved
    else:
        assert result.status_code == 202
        assert json.loads(result.body)['operation_id'] == 'original-operation'


def test_engine_position_hint_is_prepared_outside_write_and_forwarded_unchanged(engine_store, monkeypatch):
    from app import command_dispatch, game_analysis_commands
    from fastapi.testclient import TestClient
    selected = []
    dispatched = []
    plan = {'game_id': 'available-game', 'analysis_version': 1, 'kind': 'search'}
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(main, 'read_operation', lambda *_args, **_options: {'state': 'unknown'})
    monkeypatch.setattr(game_analysis_commands, 'prepare_position_claim',
        lambda preferred_game_id: selected.append(preferred_game_id) or plan)
    monkeypatch.setattr(command_dispatch, 'dispatch_command',
        lambda command, payload, **options: dispatched.append((command, payload, options)) or {'job': None})
    response = TestClient(main.app).post('/api/games/analysis/position/claim',
        json={'preferred_game_id': 'available-game'},
        headers={'X-Tempo-Engine-Worker': 'docker', 'X-Tempo-Work-Class': 'background', 'Idempotency-Key': 'hint-operation'})
    assert response.json() == {'job': None}
    assert selected == ['available-game']
    assert dispatched == [('games.analysis.position.claim', {**plan, 'claim_request': {'preferred_game_id': 'available-game'}},
                           {'idempotency_key': 'hint-operation', 'background': True})]
