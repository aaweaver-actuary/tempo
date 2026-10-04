"""Defensive-only pause preserves shared recommendation and foreground work."""
from datetime import datetime, timezone
import json
import pytest
from app.services.database_executor import database_writer

from app import database, main, settings_commands
from app.models import Settings
from app.services import durable_tasks, background_activity, threat_pipeline
from app.services.threat_detection import find_defensive_knight_forks
from app.services.threat_models import GameSnapshot, SourceLine


def initialize_pause_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'pause.db')
    database.initialize()


def test_disabled_defensive_analysis_does_not_claim_exercise_search(tmp_path, monkeypatch):
    initialize_pause_database(tmp_path, monkeypatch)
    seed_exercise_requests()
    assert threat_pipeline.claim_analysis_request() is None
    with database.read_connection() as connection:
        requests = connection.execute('SELECT state,attempts FROM threat_analysis_requests').fetchall()
    assert requests and all(tuple(row) == ('queued', 0) for row in requests)


def seed_exercise_requests():
    fen = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"
    game = GameSnapshot("lichess:pause-test", 1, fen, ("a2a3",), "white")
    seed = find_defensive_knight_forks(game, SourceLine("engine", 1,
        ("b4c2", "e1d2", "c2a1", "d2c1")))[0]
    with database.connection() as connection:
        connection.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
            result,start_fen,moves_json,analysis_state,analysis_version)
            VALUES(?,'lichess','learner',?,'rapid',1,'white','0-1',?,?,'ready',1)""",
            (game.game_id, datetime.now(timezone.utc).isoformat(), fen, json.dumps(game.moves_uci)))
        threat_pipeline._upsert_seed(connection, game, seed)
        return connection.execute('SELECT id FROM threat_analysis_requests ORDER BY id LIMIT 1').fetchone()[0]


def attach_recommendation(request_id, *, coverage=False):
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('pause-rep','Pause','pause.pgn',?)", (now,))
        connection.execute("""INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,score,
            evidence_json,evidence_fingerprint,created_at,updated_at)
            VALUES('pause-opportunity','pause-rep','missing_response','pause-position',1,'{}','pause-evidence',?,?)""", (now, now))
        if coverage:
            connection.execute("""INSERT INTO repertoire_coverage_runs(id,repertoire_id,settings_json,
                status,created_at,updated_at) VALUES('pause-run','pause-rep','{}','complete',?,?)""", (now, now))
            connection.execute("""INSERT INTO repertoire_coverage_nodes(id,run_id,repertoire_id,fen,fen_key,trained_color,routes_json,covered_replies_json,
                ply,updated_at)
                VALUES('pause-node','pause-run','pause-rep','fen','key','white','[]','[]',0,?)""", (now,))
            connection.execute("""INSERT INTO coverage_discovery_recommendation_requests(opportunity_id,request_id,
                coverage_node_id,route_json,created_at,updated_at) VALUES('pause-opportunity',?,'pause-node','[]',?,?)""",
                (request_id, now, now))
        else:
            connection.execute("""INSERT INTO discovery_recommendation_requests(opportunity_id,request_id,
                source_game_id,source_ply,created_at,updated_at) VALUES('pause-opportunity',?,'lichess:pause-test',0,?,?)""",
                (request_id, now, now))


@pytest.mark.parametrize('coverage', [False, True])
def test_paused_defense_keeps_shared_repertoire_recommendation_claimable(tmp_path, monkeypatch, coverage):
    initialize_pause_database(tmp_path, monkeypatch)
    request_id = seed_exercise_requests()
    attach_recommendation(request_id, coverage=coverage)
    claimed = threat_pipeline.claim_analysis_request()
    assert claimed['id'] == request_id
    control = main.defensive_engine_control(request_id, claimed['lease_id'])
    assert control['search_allowed'] is True
    assert main.defensive_engine_control(request_id, 'stale-lease')['search_allowed'] is False
    with database.connection() as connection:
        connection.execute("UPDATE repertoire_opportunities SET status='dismissed'")
    assert main.defensive_engine_control(request_id, claimed['lease_id'])['search_allowed'] is False


def test_defensive_control_pause_resume_and_read_only_replay(tmp_path, monkeypatch):
    initialize_pause_database(tmp_path, monkeypatch)
    request_id = seed_exercise_requests()
    with database.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
    claimed = threat_pipeline.claim_analysis_request()
    with database.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=0')
    before = main.defensive_engine_control(request_id, claimed['lease_id'])
    assert before == {'foreground_active': False, 'search_allowed': False}
    assert main.defensive_engine_control(request_id, claimed['lease_id']) == before
    with database.read_connection() as connection:
        row = connection.execute('SELECT state,lease_id,attempts FROM threat_analysis_requests WHERE id=?', (request_id,)).fetchone()
    assert tuple(row) == ('leased', claimed['lease_id'], 1)
    with database.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
    assert main.defensive_engine_control(request_id, claimed['lease_id'])['search_allowed'] is True
    assert main.defensive_engine_control('missing-request', 'lease')['search_allowed'] is False


def test_defensive_pause_setting_survives_legacy_omission_and_restart(tmp_path, monkeypatch):
    initialize_pause_database(tmp_path, monkeypatch)
    assert main.get_settings().defensive_analysis_enabled is False
    database_writer.start()
    try:
        main.put_settings(Settings(defensive_analysis_enabled=True, include_defensive_cards_in_daily_stack=False))
        main.put_settings(Settings(new_cards_per_day=12))
        assert main.get_settings().defensive_analysis_enabled is True
        assert main.get_settings().include_defensive_cards_in_daily_stack is False
    finally:
        database_writer.stop()
    database.initialize()
    assert main.get_settings().defensive_analysis_enabled is True


def test_defensive_pause_migrates_existing_sqlite_without_losing_requests(tmp_path, monkeypatch):
    initialize_pause_database(tmp_path, monkeypatch)
    request_id = seed_exercise_requests()
    with database.connection() as connection:
        connection.execute('ALTER TABLE settings DROP COLUMN defensive_analysis_enabled')
    database.initialize()
    assert main.get_settings().defensive_analysis_enabled is False
    with database.read_connection() as connection:
        assert connection.execute('SELECT state FROM threat_analysis_requests WHERE id=?', (request_id,)).fetchone()[0] == 'queued'


@pytest.mark.parametrize('kind', [
    'defensive_threat_scan', 'defensive_threat_validate', 'defensive_threat_backfill',
    'defensive_threat_report_audit', 'defensive_rubric_audit', 'defensive_admission',
])
def test_defensive_slices_pause_before_claim_and_redispatched_work_replays_once(tmp_path, monkeypatch, kind):
    initialize_pause_database(tmp_path, monkeypatch)
    database_writer.start()
    try:
        queued = durable_tasks.enqueue_task(kind, 'pause-test', {})
        assert durable_tasks.claim_task(kind) is None
        with database.connection() as connection:
            connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
        claimed = durable_tasks.claim_task(kind)
        with database.connection() as connection:
            connection.execute('UPDATE settings SET defensive_analysis_enabled=0')
        assert durable_tasks.defer_paused_defensive_task(claimed)
        assert durable_tasks.defer_paused_defensive_task(claimed)
        assert durable_tasks.claim_task(kind) is None
        with database.read_connection() as connection:
            row = connection.execute('SELECT state,attempt_count,generation FROM background_tasks WHERE id=?', (queued['id'],)).fetchone()
        assert tuple(row) == ('queued', 0, claimed['generation'])
        foreground = durable_tasks.enqueue_task('daily_queue', 'foreground-still-ready', {})
        assert durable_tasks.claim_task()['id'] == foreground['id']
        with database.connection() as connection:
            connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
        resumed = durable_tasks.claim_task(kind)
        assert resumed['id'] == claimed['id']
        assert durable_tasks.complete_task(resumed['id'], resumed['generation'], resumed['lease_token'])
        assert durable_tasks.claim_task(kind) is None
    finally:
        database_writer.stop()


def test_defensive_pause_activity_labels_recommendations_and_preserved_work(tmp_path, monkeypatch):
    initialize_pause_database(tmp_path, monkeypatch)
    request_id = seed_exercise_requests()
    attach_recommendation(request_id)
    items = background_activity.list_activity()['items']
    recommendation = next(item for item in items if item['source'] == 'threat_analysis' and item['id'] == request_id)
    assert recommendation['title'] == 'Repertoire recommendation search'
    assert recommendation['paused'] is False
    other = next(item for item in items if item['source'] == 'threat_analysis' and item['id'] != request_id)
    assert other['state'] == 'paused'


def test_postgres_defensive_setting_preserves_omitted_enabled_value(monkeypatch):
    from types import SimpleNamespace
    existing = Settings(defensive_analysis_enabled=True, include_defensive_cards_in_daily_stack=False).model_dump()
    statements = []
    class RecordingDatabase:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: existing, rowcount=1)
    monkeypatch.setattr(settings_commands, 'request_queue_refresh_in_transaction', lambda *_: None)
    result = settings_commands.update_settings(RecordingDatabase(), {
        'settings': Settings(new_cards_per_day=12).model_dump(), 'supplied_fields': ['new_cards_per_day']})
    assert result['defensive_analysis_enabled'] is True
    assert result['include_defensive_cards_in_daily_stack'] is False
    assert statements[1][1][list(settings_commands._SETTINGS_COLUMNS).index('defensive_analysis_enabled')] == 1


def test_paused_defensive_celery_delivery_never_computes_or_wakes_retries(tmp_path, monkeypatch):
    from app import tasks
    initialize_pause_database(tmp_path, monkeypatch)
    database_writer.start()
    computations, wakeups = [], []
    monkeypatch.setattr(tasks, 'execute_threat_scan_slice', lambda task: computations.append(task))
    monkeypatch.setattr(tasks.celery_app, 'send_task', lambda *args, **kwargs: wakeups.append(args))
    try:
        durable_tasks.enqueue_task('defensive_threat_scan', 'celery-pause', {})
        with database.connection() as connection:
            connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
        claimed = durable_tasks.claim_task('defensive_threat_scan')
        with database.connection() as connection:
            connection.execute('UPDATE settings SET defensive_analysis_enabled=0')
        assert tasks.execute_background_slice.run(claimed) is False
        assert tasks.execute_background_slice.run(claimed) is False
        assert computations == wakeups == []
        with database.connection() as connection:
            connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
        resumed = durable_tasks.claim_task('defensive_threat_scan')
        tasks.execute_background_slice.run(resumed)
        assert computations == [resumed]
    finally:
        database_writer.stop()


def test_pausing_engine_release_preserves_retry_budget_and_idempotent_resume(tmp_path, monkeypatch):
    from app.models import GameAnalysisLeaseRequest
    initialize_pause_database(tmp_path, monkeypatch)
    request_id = seed_exercise_requests()
    with database.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
    claimed = threat_pipeline.claim_analysis_request()
    with database.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=0')
    lease = GameAnalysisLeaseRequest(lease_id=claimed['lease_id'])
    assert main.release_defensive_threat_analysis(request_id, lease)['status'] == 'queued'
    assert main.release_defensive_threat_analysis(request_id, lease)['status'] == 'stale'
    with database.read_connection() as connection:
        assert connection.execute('SELECT attempts FROM threat_analysis_requests WHERE id=?', (request_id,)).fetchone()[0] == 0
    with database.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
    assert threat_pipeline.claim_analysis_request()['id'] == request_id


def test_defensive_engine_control_preempts_foreground_without_waiting_for_database(monkeypatch):
    def forbidden_background_read(**options):
        raise AssertionError('Foreground preemption must not wait for a database connection')
    monkeypatch.setattr(main, 'background_read_connection', forbidden_background_read)
    with main.activity_gate.foreground():
        assert main.defensive_engine_control('request', 'lease') == {
            'foreground_active': True, 'search_allowed': False}
