from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
import logging
import time
import threading

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.main import lifespan
from app.main import enqueue_daily_queue_refresh
from app.services.database_executor import DatabaseWriter
from app.services.durable_tasks import complete_task, enqueue_task


def _seed_due_tactic() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES('__tactics__','Tactics','built-in',?)",
            (now,),
        )
        connection.execute(
            """INSERT INTO cards(
                   id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,
                   state,trained_color,source_ref
               ) VALUES('durable-tactic','__tactics__','prefix',?,?,'2000-01-01',
                        'tactics','learning','white','durable-puzzle')""",
            (
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                json.dumps(["e2e4"]),
            ),
        )


def _wait_for_queue_count(client: TestClient, expected: int) -> dict:
    payload = None
    for _ in range(200):
        payload = client.get("/api/queue/today").json()
        if payload["count"] == expected and payload["projection"]["state"] == "ready":
            return payload
        time.sleep(0.01)
    assert payload is not None
    return payload


def test_concurrent_queue_progress_repertoire_and_settings_reads_never_return_busy(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_due_tactic()
        assert client.post("/api/system/tasks/daily-queue:today/retry").status_code in {200, 404}
        paths = ["/api/queue/today", "/api/progress", "/api/repertoires", "/api/settings"] * 10
        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(pool.map(client.get, paths))
    assert all(response.status_code == 200 for response in responses)


def test_workspace_reads_complete_under_one_second_during_full_background_backlog(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        now = datetime.now(timezone.utc).isoformat()
        queue_date = date.today().isoformat()
        with database.connection() as connection:
            connection.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('production-scale','Production scale','scale.pgn',?)",
                (now,),
            )
            connection.execute(
                "INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at) VALUES('production-scale','clean',?)",
                (now,),
            )
            connection.executemany(
                "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,'production-scale','line','white',?,'[]',?)",
                [
                    (
                        f"scale-line-{index}",
                        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                        now,
                    )
                    for index in range(1_600)
                ],
            )
            connection.executemany(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date)
                   VALUES(?,'production-scale','prefix',?,'[]',?)""",
                [
                    (
                        f"scale-card-{index}",
                        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                        queue_date,
                    )
                    for index in range(850)
                ],
            )
            connection.executemany(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('production-scale',?)",
                [(f"scale-card-{index}",) for index in range(850)],
            )
            connection.executemany(
                "INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES(?,?,?,'queued')",
                [(queue_date, f"scale-card-{index}", index) for index in range(850)],
            )
        for index in range(50):
            enqueue_task(
                "test",
                f"backlog-{index}",
                {"index": index},
                delay_seconds=3600,
            )
        started = time.perf_counter()
        responses = [
            client.get("/api/queue/today"),
            client.get("/api/progress"),
            client.get("/api/repertoires"),
            client.get("/api/settings"),
        ]
        elapsed = time.perf_counter() - started
    assert all(response.status_code == 200 for response in responses)
    assert elapsed < 1.0


def test_get_endpoints_are_query_only(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_due_tactic()
        before = database.DB_PATH.read_bytes()
        for path in ("/api/queue/today", "/api/progress", "/api/repertoires", "/api/settings"):
            assert client.get(path).status_code == 200
        with database.read_connection() as read_database:
            assert read_database.execute("PRAGMA query_only").fetchone()[0] == 1
        after = database.DB_PATH.read_bytes()
    assert before == after


def test_daily_queue_admission_is_single_flight_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_due_tactic()
        for _ in range(5):
            enqueue_daily_queue_refresh()
        assert _wait_for_queue_count(client, 1)["count"] == 1
        with database.connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM daily_queue WHERE queue_date=? AND card_id='durable-tactic'",
                (date.today().isoformat(),),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM background_tasks WHERE kind='daily_queue' AND deduplication_key=?",
                ("current",),
            ).fetchone()[0] == 1


def test_terminal_task_failure_is_visible_and_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as connection:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO background_tasks(
                       id,kind,deduplication_key,generation,priority,state,phase,
                       payload_version,payload_json,attempt_count,max_attempts,
                       next_attempt_at,last_error,created_at,updated_at
                   ) VALUES('failed-task','test','visible',1,100,'failed','failed',
                            1,'{}',5,5,?,'sanitized failure',?,?)""",
                (now, now, now),
            )
        tasks = client.get("/api/system/tasks").json()["tasks"]
        assert next(task for task in tasks if task["id"] == "failed-task")["last_error"] == "sanitized failure"
        retried = client.post("/api/system/tasks/failed-task/retry")
        assert retried.status_code == 200
        assert retried.json()["state"] == "queued"


def test_repeated_triggers_coalesce_by_kind_key_and_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        first = enqueue_task("test", "same-input", {"value": 1}, delay_seconds=3600)
        second = enqueue_task("test", "same-input", {"value": 2}, delay_seconds=3600)
        with database.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM background_tasks WHERE kind='test' AND deduplication_key='same-input'"
            ).fetchall()
    assert len(rows) == 1
    assert second["id"] == first["id"]
    assert second["generation"] == first["generation"] + 1
    assert json.loads(rows[0]["payload_json"]) == {"value": 2}


def test_durable_task_replays_once_after_process_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as connection:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO background_tasks(
                       id,kind,deduplication_key,generation,priority,state,phase,
                       payload_version,payload_json,attempt_count,max_attempts,next_attempt_at,
                       lease_token,lease_expires_at,created_at,updated_at
                   ) VALUES('interrupted','test','restart',1,100,'leased','computing',
                            1,'{}',1,5,'2999-01-01T00:00:00+00:00','old-lease',?, ?,?)""",
                (now, now, now),
            )
    with TestClient(app):
        with database.read_connection() as connection:
            row = connection.execute(
                "SELECT state,lease_token,attempt_count FROM background_tasks WHERE id='interrupted'"
            ).fetchone()
    assert dict(row) == {"state": "queued", "lease_token": None, "attempt_count": 1}


def test_stale_task_generation_cannot_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as connection:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO background_tasks(
                       id,kind,deduplication_key,generation,priority,state,phase,
                       payload_version,payload_json,attempt_count,max_attempts,next_attempt_at,
                       lease_token,lease_expires_at,created_at,updated_at
                   ) VALUES('stale-task','test','stale',2,100,'leased','computing',
                            1,'{}',1,5,?,'new-lease',?, ?,?)""",
                (now, now, now, now),
            )
        assert complete_task("stale-task", 1, "old-lease") is False
        with database.read_connection() as connection:
            state = connection.execute(
                "SELECT state FROM background_tasks WHERE id='stale-task'"
            ).fetchone()[0]
    assert state == "leased"


def test_foreground_review_preempts_queued_background_commits(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    writer = DatabaseWriter()
    writer.start()
    first_started = threading.Event()
    release_first = threading.Event()
    order: list[str] = []

    def first_background(connection):
        first_started.set()
        assert release_first.wait(timeout=2)
        order.append("first-background")

    def record(label):
        return lambda connection: order.append(label)

    first_thread = threading.Thread(
        target=lambda: writer.submit_background_write(first_background, label="first")
    )
    second_thread = threading.Thread(
        target=lambda: writer.submit_background_write(record("second-background"), label="second")
    )
    foreground_thread = threading.Thread(
        target=lambda: writer.submit_foreground_write(record("foreground-review"), label="review")
    )
    first_thread.start()
    assert first_started.wait(timeout=1)
    second_thread.start()
    foreground_thread.start()
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    foreground_thread.join(timeout=2)
    writer.stop()
    assert order == ["first-background", "foreground-review", "second-background"]


def test_background_commit_budget_is_enforced_at_production_scale(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    writer = DatabaseWriter()
    writer.start()
    with caplog.at_level(logging.WARNING, logger="tempo.writer"):
        writer.submit_background_write(
            lambda connection: time.sleep(0.06),
            label="production-scale-publication",
        )
    writer.stop()
    assert "background commit exceeded 50ms budget" in caplog.text


def test_last_published_queue_remains_playable_during_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_due_tactic()
        enqueue_daily_queue_refresh()
        assert _wait_for_queue_count(client, 1)["cards"][0]["id"] == "durable-tactic"
        with database.connection() as connection:
            connection.execute(
                "UPDATE queue_projections SET state='refreshing',refresh_pending=1 WHERE queue_date=?",
                (date.today().isoformat(),),
            )
        refreshing = client.get("/api/queue/today").json()
    assert refreshing["projection"]["state"] == "refreshing"
    assert refreshing["cards"][0]["id"] == "durable-tactic"


def test_needs_repair_openings_do_not_hide_or_inflate_due_tactics(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_due_tactic()
        now = datetime.now(timezone.utc).isoformat()
        with database.connection() as connection:
            connection.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('broken','Broken','broken.pgn',?)",
                (now,),
            )
            connection.execute(
                "INSERT INTO repertoire_integrity_state(repertoire_id,status) VALUES('broken','needs_repair')"
            )
            connection.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color)
                   VALUES('broken-opening','broken','prefix',?,?,'learning','2000-01-01','opening','white')""",
                (
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    json.dumps(["e2e4"]),
                ),
            )
        enqueue_daily_queue_refresh()
        queue = _wait_for_queue_count(client, 1)
        progress = client.get("/api/progress").json()
    assert [card["id"] for card in queue["cards"]] == ["durable-tactic"]
    assert progress["dueToday"] == 1


def test_daily_queue_remains_exact_across_restart_and_midday_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        _seed_due_tactic()
        enqueue_daily_queue_refresh()
        first = _wait_for_queue_count(client, 1)
        first_entry_ids = [card["queue_entry_id"] for card in first["cards"]]
    with TestClient(app) as client:
        after_restart = client.get("/api/queue/today").json()
        assert [card["queue_entry_id"] for card in after_restart["cards"]] == first_entry_ids
        with database.connection() as connection:
            connection.execute(
                """INSERT INTO cards(
                       id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,
                       state,trained_color,source_ref
                   ) VALUES('midday-tactic','__tactics__','prefix',?,?,'2000-01-01',
                            'tactics','learning','white','midday-puzzle')""",
                (
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    json.dumps(["d2d4"]),
                ),
            )
        enqueue_daily_queue_refresh()
        midday = _wait_for_queue_count(client, 2)
    assert len({card["id"] for card in midday["cards"]}) == 2
    assert first_entry_ids[0] in [card["queue_entry_id"] for card in midday["cards"]]


def test_multi_worker_api_is_rejected_until_database_owner_is_cross_process(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    context = lifespan(app)
    try:
        import asyncio

        asyncio.run(context.__aenter__())
    except RuntimeError as error:
        assert "one API worker" in str(error)
    else:
        raise AssertionError("multi-worker startup must fail")
