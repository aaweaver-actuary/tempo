"""Engine selection reserves ordinary games without losing interactive demand."""
from datetime import datetime, timezone

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


def test_engine_claim_rollback_preserves_both_request_and_scheduling_turn(engine_store):
    from app.services.engine_scheduling import admit_automated_selection, record_ordinary_selection
    class Interrupted(Exception):
        pass
    with pytest.raises(Interrupted), database.connection() as connection:
        assert admit_automated_selection(connection, datetime.now(timezone.utc).isoformat())
        raise Interrupted()
    with database.read_connection() as connection:
        assert connection.execute('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 0
    with pytest.raises(Interrupted), database.connection() as connection:
        connection.execute('UPDATE engine_scheduling_state SET automated_streak=3')
        record_ordinary_selection(connection)
        raise Interrupted()
    with database.read_connection() as connection:
        assert connection.execute('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 0
