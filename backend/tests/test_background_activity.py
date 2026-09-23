"""Named regressions for the background activity tray contract."""

from datetime import datetime, timezone
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.background_activity import list_activity, report_progress, set_control
from app.services.database_executor import database_writer
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
        db.execute("INSERT INTO repertoire_priority_jobs(repertoire_id,generation,status,next_attempt_at,updated_at) VALUES('activity-rep',1,'queued',?,?)", (now, now))
        db.execute("INSERT INTO game_analysis_jobs(game_id,status,updated_at) VALUES('activity-game','queued',?)", (now,))


def test_activity_projection_lists_every_background_source_and_pages_without_losing_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    _seed_all_sources()
    client = TestClient(app)
    first = client.get("/api/system/activity?limit=3").json()
    all_items = client.get("/api/system/activity?limit=100").json()["items"]
    assert {item["source"] for item in all_items} == {
        "durable", "sync", "derivation", "integrity", "coverage",
        "statistics", "priority", "game_analysis",
    }
    assert first["total"] == 8
    assert len(first["items"]) == 3
    assert first["next_offset"] == 3
    integrity = next(item for item in all_items if item["source"] == "integrity")
    assert (integrity["completed"], integrity["total"]) == (2, 5)


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
        assert list_activity()["counts"]["paused"] >= 1
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
    with database.connection() as db:
        db.execute("UPDATE game_analysis_jobs SET status='leased',lease_id='current-lease' WHERE game_id='activity-game'")
    assert not report_progress("game_analysis", "activity-game", "1:1", "Stale", 1, 2, lease_id="old-lease")
    assert report_progress("game_analysis", "activity-game", "1:1", "Scanning", 1, 2, lease_id="current-lease")
    assert set_control("game_analysis", "activity-game", "pause")
    assert not report_progress("game_analysis", "activity-game", "1:1", "Scanning", 2, 2, lease_id="current-lease")
    assert list_activity()["items"][0]["phase"] != "Stale"


def test_pausing_coverage_invalidates_browser_lease_and_resume_reopens_queue(tmp_path, monkeypatch):
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
    response = client.post("/api/system/activity/control", json={
        "source": "coverage", "id": "activity-coverage", "action": "pause",
    })
    assert response.status_code == 200
    assert client.post("/api/repertoire-coverage/maia/heartbeat", json={
        "node_id": "activity-node", "lease_id": "old-lease",
    }).status_code == 409
    with database.read_connection() as db:
        node = db.execute("SELECT maia_status,lease_id FROM repertoire_coverage_nodes WHERE id='activity-node'").fetchone()
    assert tuple(node) == ("queued", None)
    assert client.post("/api/system/activity/control", json={
        "source": "coverage", "id": "activity-coverage", "action": "resume",
    }).status_code == 200
    assert next(item for item in list_activity()["items"] if item["source"] == "coverage")["state"] == "queued"


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
        "refresh_gameplay_events", "refresh_game_features", "enqueue_priority_refreshes_for_game",
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
    assert tuple(completed) == ("complete", 7)
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


def test_activity_projection_pages_one_thousand_queued_jobs_without_a_sweep_write(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as db:
        db.executemany(
            """INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,
               payload_version,payload_json,attempt_count,max_attempts,next_attempt_at,created_at,updated_at)
               VALUES(?,?,?,1,100,'queued','queued',1,'{}',0,5,?,?,?)""",
            [(f"large-{index}", "activity_fixture", f"large-{index}", now, now, now)
             for index in range(1_000)],
        )
    started = time.perf_counter()
    page = list_activity(limit=50)
    assert time.perf_counter() - started < 2.0
    assert page["total"] == 1_000
    assert len(page["items"]) == 50
    assert page["next_offset"] == 50
