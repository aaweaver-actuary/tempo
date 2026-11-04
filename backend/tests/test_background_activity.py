"""Named regressions for the background activity tray contract."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from tempo_legacy_database import connections as database
from app.main import app
from app.services.background_activity import report_progress, set_control
from tempo_legacy_database.adapters.database_executor import database_writer
from app.services.durable_tasks import claim_task, complete_task, enqueue_task, requeue_interrupted_tasks
from app.services import game_sync_coordinator


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _seed_all_sources() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('activity-rep','Activity','fixture',?)", (now,))
        db.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                   VALUES('activity-game','lichess','tester',?,'rapid',1,'white','win',?,'[]')""", (now, START_FEN))
        db.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,
                   payload_version,payload_json,attempt_count,max_attempts,next_attempt_at,created_at,updated_at)
                   VALUES('activity-task','daily_queue','activity',1,100,'queued','queued',1,'{}',0,5,?,?,?)""", (now, now, now))
        db.execute("INSERT INTO game_sync_jobs(id,request_json,status,created_at,updated_at) VALUES('activity-sync','{}','queued',?,?)", (now, now))
        db.execute("INSERT INTO game_derivation_jobs(game_id,status,updated_at) VALUES('activity-game','queued',?)", (now,))
        db.execute("""INSERT INTO repertoire_integrity_jobs(repertoire_id,run_id,status,source_offset,total_sources,updated_at)
                   VALUES('activity-rep','activity-scan','running',2,5,?)""", (now,))
        db.execute("""INSERT INTO repertoire_coverage_runs(id,repertoire_id,status,settings_json,total_nodes,completed_nodes,created_at,updated_at)
                   VALUES('activity-coverage','activity-rep','queued','{}',0,0,?,?)""", (now, now))
        db.execute("INSERT INTO daily_statistics_jobs(local_day,status,updated_at) VALUES('2026-09-22','queued',?)", (now,))


def test_retired_tray_routes_leave_existing_pause_metadata_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    _seed_all_sources()
    assert set_control("durable", "activity-task", "pause")
    with database.read_connection() as db:
        before = tuple(db.execute("SELECT * FROM background_activity WHERE source='durable' AND work_id='activity-task'").fetchone())
    client = TestClient(app)
    assert client.get("/api/system/activity").status_code == 404
    assert client.post("/api/system/activity/control", json={"source": "durable", "id": "activity-task", "action": "resume"}).status_code == 404
    with database.read_connection() as db:
        assert tuple(db.execute("SELECT * FROM background_activity WHERE source='durable' AND work_id='activity-task'").fetchone()) == before
    database_writer.start()
    try:
        assert claim_task("daily_queue") is None
    finally:
        database_writer.stop()


def test_activity_controls_survive_restart_and_prioritize_only_their_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    database_writer.start()
    try:
        first = enqueue_task("activity_fixture", "first", {})
        second = enqueue_task("activity_fixture", "second", {})
        assert set_control("durable", first["id"], "pause")
        assert set_control("durable", second["id"], "prioritize")
        claimed = claim_task("activity_fixture")
        assert claimed and claimed["id"] == second["id"]
        requeue_interrupted_tasks()
        with database.read_connection() as db:
            assert db.execute("SELECT paused FROM background_activity WHERE source='durable' AND work_id=?", (first["id"],)).fetchone()[0] == 1
        assert set_control("durable", first["id"], "resume")
        claimed_again = claim_task("activity_fixture")
        assert claimed_again and claimed_again["id"] == second["id"]
        assert complete_task(second["id"], claimed["generation"], claimed["lease_token"]) is False
        assert complete_task(second["id"], claimed_again["generation"], claimed_again["lease_token"]) is True
        assert claim_task("activity_fixture")["id"] == first["id"]
    finally:
        database_writer.stop()


def test_activity_progress_rejects_stale_generation_and_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    _seed_all_sources()
    assert report_progress("integrity", "activity-rep", "activity-scan", "Scanning", 2, 5)
    assert not report_progress("integrity", "activity-rep", "old-scan", "Stale", 5, 5)
    assert not set_control("game_analysis", "activity-game", "pause")
    with pytest.raises(ValueError, match="Unknown background work source"):
        report_progress("game_analysis", "activity-game", "1:1", "Scanning", 1, 2)
    with database.read_connection() as db:
        assert tuple(db.execute("SELECT phase,completed_units,total_units FROM background_activity WHERE source='integrity' AND work_id='activity-rep'").fetchone()) == ("Scanning", 2, 5)



def test_retired_coverage_heartbeat_does_not_recreate_work_and_control_remains(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    _seed_all_sources()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        db.execute(
            """INSERT INTO repertoire_coverage_nodes(id,run_id,repertoire_id,fen,fen_key,ply,
               trained_color,routes_json,covered_replies_json,explorer_status,maia_status,
               lease_id,lease_expires_at,updated_at)
               VALUES('activity-node','activity-coverage','activity-rep',?,'start',0,'white','[]','[]',
                      'complete','leased','old-lease',?,?)""",
            (START_FEN, now, now),
        )
    client = TestClient(app)
    assert set_control("coverage", "activity-coverage", "pause")
    with database.read_connection() as db:
        before = tuple(db.execute("SELECT maia_status,lease_id FROM repertoire_coverage_nodes WHERE id='activity-node'").fetchone())
        count_before = db.execute("SELECT COUNT(*) FROM repertoire_coverage_nodes").fetchone()[0]
    assert client.post("/api/repertoire-coverage/maia/heartbeat", json={
        "node_id": "activity-node", "lease_id": "old-lease",
    }).status_code == 404
    with database.read_connection() as db:
        node = db.execute("SELECT maia_status,lease_id FROM repertoire_coverage_nodes WHERE id='activity-node'").fetchone()
        assert db.execute("SELECT COUNT(*) FROM repertoire_coverage_nodes").fetchone()[0] == count_before
    assert tuple(node) == before == ("queued", None)
    assert set_control("coverage", "activity-coverage", "resume")
    with database.read_connection() as db:
        assert db.execute("SELECT paused FROM background_activity WHERE source='coverage' AND work_id='activity-coverage'").fetchone()[0] == 0


def test_derivation_pauses_after_a_phase_and_resumes_without_replaying_completed_work(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    _seed_all_sources()
    first_phase_calls = []

    def finish_first_phase(game_id):
        first_phase_calls.append(game_id)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(set_control, "derivation", game_id, "pause").result(timeout=3)

    monkeypatch.setattr(game_sync_coordinator, "_index_game_positions", finish_first_phase)
    for operation_name in (
        "compare_games", "refresh_game_findings", "apply_real_game_misses",
        "refresh_gameplay_events", "refresh_game_features",
    ):
        monkeypatch.setattr(game_sync_coordinator, operation_name, lambda *args, **kwargs: None)
    with database.connection() as db:
        db.execute("UPDATE game_derivation_jobs SET status='running' WHERE game_id='activity-game'")
    game_sync_coordinator._execute_derivation("activity-game")
    with database.read_connection() as db:
        paused = db.execute("SELECT status,completed_phases FROM game_derivation_jobs WHERE game_id='activity-game'").fetchone()
    assert tuple(paused) == ("queued", 1)
    assert set_control("derivation", "activity-game", "resume")
    with database.connection() as db:
        db.execute("UPDATE game_derivation_jobs SET status='running' WHERE game_id='activity-game'")
    game_sync_coordinator._execute_derivation("activity-game")
    with database.read_connection() as db:
        completed = db.execute("SELECT status,completed_phases FROM game_derivation_jobs WHERE game_id='activity-game'").fetchone()
    assert tuple(completed) == ("complete", 6)
    assert first_phase_calls == ["activity-game"]


def test_activity_progress_keeps_foreground_settings_read_responsive(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    _seed_all_sources()
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(lambda: [report_progress("integrity", "activity-rep", "activity-scan", "Scanning", index, 20) for index in range(20)])
        client = TestClient(app)
        assert client.get("/api/settings").status_code == 200
        writer.result(timeout=5)


def test_sqlite_restart_preserves_old_engine_rows_without_bootstrapping_new_work(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='engine_scheduling_state'").fetchone() is None
        for game_id in ('old', 'new'):
            db.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                VALUES(?,'lichess','test',?,'rapid',1,'white','win',?,'[\"e2e4\"]')""", (game_id, now, START_FEN))
        db.execute("ALTER TABLE game_analysis_jobs ADD COLUMN pending_since TEXT")
        db.execute("ALTER TABLE game_analysis_jobs ADD COLUMN generation_started_at TEXT")
        db.execute("ALTER TABLE game_analysis_jobs ADD COLUMN age_origin_estimated INTEGER NOT NULL DEFAULT 0")
        db.execute("CREATE TABLE engine_scheduling_state(id INTEGER PRIMARY KEY, automated_streak INTEGER)")
        db.execute("INSERT INTO engine_scheduling_state VALUES(1,3)")
        db.execute("INSERT INTO game_analysis_jobs(game_id,analysis_version,status,updated_at) VALUES('old',3,'complete',?)", (now,))
        before = tuple(db.execute("SELECT * FROM game_analysis_jobs WHERE game_id='old'").fetchone())
    database.initialize()
    with database.read_connection() as db:
        rows = db.execute('SELECT * FROM game_analysis_jobs').fetchall()
        assert len(rows) == 1 and tuple(rows[0]) == before
        assert tuple(db.execute("SELECT * FROM engine_scheduling_state").fetchone()) == (1, 3)
        assert db.execute("SELECT COUNT(*) FROM imported_games WHERE id IN ('old','new')").fetchone()[0] == 2
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='game_age_insert'").fetchone() is None
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_background_tasks_diagnostic_state'").fetchone()


def test_discovery_stale_envelope_does_not_complete_its_live_lease():
    assert not game_sync_coordinator._complete_after_handler('discovery_admission', True)
    assert game_sync_coordinator._complete_after_handler('discovery_admission', False)
    assert game_sync_coordinator._complete_after_handler('ordinary', True)


def test_discovery_prerequisite_checkpoint_preserves_task_identity_and_generation(tmp_path, monkeypatch):
    from app.services.durable_tasks import advance_task_slice_in_transaction
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    database_writer.start()
    try:
        task = enqueue_task('discovery_admission', 'intent', {'intent_id': 'intent'})
        with database.connection() as db:
            db.execute("UPDATE background_tasks SET state='leased',lease_token='lease' WHERE id=?", (task['id'],))
        claimed = {**task, 'kind': 'discovery_admission', 'lease_token': 'lease', 'payload': {'intent_id': 'intent'}}
        with database.connection() as db:
            assert advance_task_slice_in_transaction(db, claimed, next_phase='queued', next_payload=claimed['payload'], eligible_at='2099-01-01T00:00:00+00:00')
        with database.read_connection() as db:
            row = db.execute('SELECT id,generation,state,lease_token,payload_json,next_attempt_at FROM background_tasks WHERE id=?', (task['id'],)).fetchone()
            assert tuple(row) == (task['id'], task['generation'], 'queued', None, '{"intent_id":"intent"}', '2099-01-01T00:00:00+00:00')
            assert db.execute("SELECT event FROM background_task_events WHERE task_id=? ORDER BY id DESC LIMIT 1", (task['id'],)).fetchone()[0] == 'slice_complete'
    finally:
        database_writer.stop()


def test_sqlite_discovery_recovery_records_specific_hold_without_changing_identity(tmp_path, monkeypatch):
    from app.services import discovery_admission
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    database.initialize()
    _seed_all_sources()
    now = datetime.now(timezone.utc).isoformat()
    database_writer.start()
    try:
        with database.connection() as db:
            db.execute("INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,status,score,evidence_json,evidence_fingerprint,created_at,updated_at) VALUES('op','activity-rep','missing_response','fen','active',1,'{}','fingerprint',?,?)", (now, now))
            db.execute("INSERT INTO discovery_admission_intents(id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,selected_move_uci,preview_moves_json,recommendation_json,line_id,created_at,updated_at) VALUES('intent','op','activity-rep','fingerprint',?,'e2e4','[\"e2e4\"]','{}','line',?,?)", (START_FEN, now, now))
        task = enqueue_task('discovery_admission', 'intent', {'intent_id': 'intent'})
        with database.connection() as db:
            db.execute("UPDATE background_tasks SET state='leased',lease_token='lease' WHERE id=?", (task['id'],))
        claimed = {**task, 'kind': 'discovery_admission', 'lease_token': 'lease', 'payload': {'intent_id': 'intent'}}
        assert discovery_admission.execute_admission_intent_slice(claimed) is True
        with database.read_connection() as db:
            row = db.execute('SELECT id,generation,state,lease_token,next_attempt_at FROM background_tasks WHERE id=?', (task['id'],)).fetchone()
            assert tuple(row) == (task['id'], task['generation'], 'queued', None, '9999-12-31T23:59:59+00:00')
            intent = db.execute("SELECT state,last_error,line_id FROM discovery_admission_intents WHERE id='intent'").fetchone()
            assert tuple(intent) == ('preparing', 'sqlite_current_authority_unavailable', 'line')
            assert db.execute("SELECT 1 FROM repertoire_lines WHERE id='line'").fetchone() is None
        # A lost checkpoint must roll back the diagnostic update as well.
        with database.connection() as db:
            db.execute("UPDATE background_tasks SET state='leased',lease_token='lease' WHERE id=?", (task['id'],))
            db.execute("UPDATE discovery_admission_intents SET last_error='prior' WHERE id='intent'")
        monkeypatch.setattr(discovery_admission, 'advance_task_slice_in_transaction', lambda *a, **k: False)
        from app.services.redis_admission_gate import BackgroundAdmissionDeferred
        with pytest.raises(BackgroundAdmissionDeferred):
            discovery_admission.execute_admission_intent_slice(claimed)
        with database.read_connection() as db:
            assert db.execute("SELECT last_error FROM discovery_admission_intents WHERE id='intent'").fetchone()[0] == 'prior'
    finally:
        database_writer.stop()
