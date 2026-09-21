import json
import time
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.game_sync_coordinator import coordinator
from app.services.repertoire_integrity import enqueue_integrity_scans
from app.services import repertoire_integrity as integrity_service


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _line(db, identifier: str, repertoire: str, moves: list[str]):
    db.execute(
        "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
        (repertoire, repertoire, "integrity.pgn", "2026-09-20T00:00:00+00:00"),
    )
    db.execute(
        "INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",
        (identifier, repertoire, identifier, "white", START, json.dumps(moves), "2026-09-20T00:00:00+00:00"),
    )


def _integrity(client, repertoire="rep"):
    enqueue_integrity_scans(repertoire)
    coordinator.wake()
    for _ in range(100):
        payload = client.get(f"/api/repertoires/{repertoire}/integrity").json()
        if payload.get("scan_status") in {"idle", "failed"}:
            return payload
        time.sleep(0.01)
    return payload


def _wait_for_repair(client, task_id: str, repertoire: str = "rep") -> dict:
    task_payload = None
    integrity_payload = None
    for _ in range(300):
        tasks = client.get("/api/system/tasks").json()["tasks"]
        task_payload = next(item for item in tasks if item["id"] == task_id)
        integrity_payload = client.get(
            f"/api/repertoires/{repertoire}/integrity"
        ).json()
        if task_payload["state"] == "failed":
            raise AssertionError(task_payload["last_error"])
        if (
            task_payload["state"] == "complete"
            and integrity_payload["scan_status"] == "idle"
        ):
            return integrity_payload
        time.sleep(0.01)
    raise AssertionError((task_payload, integrity_payload))


def test_repertoire_integrity_sweep_pauses_conflicting_transpositions_after_import(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5", "g1f3", "b8c6", "d2d4"])
            _line(db, "two", "rep", ["d2d4", "d7d5", "c2c4", "c7c6", "g1f3"])
        integrity = _integrity(client)
        assert integrity["status"] == "needs_repair"
        assert any(issue["kind"] == "multiple_responses" for issue in integrity["issues"])
        assert client.get("/api/queue/today").json()["cards"] == []


def test_integrity_scan_blocks_only_cards_crossing_unresolved_positions(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5", "g1f3"])
            _line(db, "two", "rep", ["e2e4", "e7e5", "f1c4"])
            for identifier, moves in (
                ("unaffected", ["e2e4"]),
                ("affected", ["e2e4", "e7e5", "g1f3"]),
            ):
                db.execute(
                    """INSERT INTO cards(
                           id,repertoire_id,kind,start_fen,moves_json,due_date,
                           content_type,trained_color
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        identifier,
                        "rep",
                        "prefix",
                        START,
                        json.dumps(moves),
                        "2026-09-20",
                        "opening",
                        "white",
                    ),
                )
                db.execute(
                    "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',?)",
                    (identifier,),
                )
        assert _integrity(client)["status"] == "needs_repair"
        with database.read_connection() as db:
            blocked = {
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT card_id FROM repertoire_integrity_card_blocks"
                )
            }
        assert blocked == {"affected"}


def test_repertoire_integrity_requires_one_response_at_player_turn_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _line(db, "one", "rep", ["e2e4", "e7e5"])
    with TestClient(app) as client:
        issues = _integrity(client)["issues"]
        assert [issue["kind"] for issue in issues] == ["missing_response"]


def test_integrity_resolution_rewrites_routes_and_truncates_losing_continuations(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5", "g1f3", "b8c6", "d2d4"])
            _line(db, "two", "rep", ["d2d4", "d7d5", "c2c4", "c7c6", "g1f3"])
        issue = next(
            item
            for item in _integrity(client)["issues"]
            if item["kind"] == "multiple_responses"
        )
        result = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": issue["signature"], "selected_move_uci": "d2d4"},
        )
        assert result.status_code == 202
        assert _wait_for_repair(client, result.json()["task_id"])["status"] == "clean"
        lines = client.get("/api/repertoire/lines").json()["lines"]
        assert any(line["moves"] == ["d2d4"] for line in lines)


def test_stale_or_illegal_integrity_resolution_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5"])
            _line(db, "two", "rep", ["d2d4", "d7d5"])
        issue = next(
            item
            for item in _integrity(client)["issues"]
            if item["kind"] == "multiple_responses"
        )
        response = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": "stale", "selected_move_uci": "d2d4"},
        )
        assert response.status_code == 409
        assert _integrity(client)["status"] == "needs_repair"


def test_legacy_integrity_issues_are_swept_in_background_and_excluded_from_training(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _line(db, "legacy", "rep", ["e2e4", "e7e5"])
    with TestClient(app) as client:
        summary = _integrity(client)
        assert summary["status"] == "needs_repair"
        assert client.get("/api/queue/today").json()["count"] == 0


def test_shared_cards_remain_trainable_only_through_clean_repertoires(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "clean-line", "clean-rep", ["e2e4"])
            _line(db, "paused-line", "paused-rep", ["e2e4", "e7e5"])
            db.execute(
                "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES(?,?,?,?,?,?,?)",
                ("shared", "clean-rep", "prefix", START, '["e2e4"]', "2026-09-20", "opening"),
            )
            db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", ("clean-rep", "shared"))
            db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", ("paused-rep", "shared"))
        _integrity(client, "clean-rep")
        _integrity(client, "paused-rep")
        client.get("/api/queue/today")
        with database.connection() as db:
            db.execute("UPDATE cards SET due_date=? WHERE id=?", ("2026-09-20", "shared"))
        queued = client.get("/api/queue/today").json()["cards"]
        assert any(card["id"] == "shared" for card in queued)


def test_invalid_sources_materialize_as_distinct_blocking_issues(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?,?,?)", ("rep", "rep", "bad.pgn", "2026-09-20", 0))
        for identifier in ("bad-one", "bad-two"):
            db.execute(
                "INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",
                (identifier, "rep", identifier, "white", START, '["not-a-uci"]', "2026-09-20"),
            )
    with TestClient(app) as client:
        issues = _integrity(client)["issues"]
        assert [issue["kind"] for issue in issues] == ["invalid_source", "invalid_source"]


def test_missing_endpoint_resolution_accepts_an_unsaved_legal_move(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _line(db, "one", "rep", ["e2e4", "e7e5"])
    with TestClient(app) as client:
        issue = _integrity(client)["issues"][0]
        response = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": issue["signature"], "selected_move_uci": "g1f3"},
        )
        assert response.status_code == 202
        assert _wait_for_repair(client, response.json()["task_id"])["status"] == "clean"


def test_repair_preserves_unchanged_card_reviews_and_archives_changed_history(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5", "g1f3"])
            _line(db, "two", "rep", ["d2d4", "d7d5", "c2c4"])
            db.execute(
                "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES(?,?,?,?,?,?,?)",
                ("losing-card", "rep", "prefix", START, '["e2e4", "e7e5", "g1f3"]', "2026-09-20", "opening"),
            )
            db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep','losing-card')")
            db.execute(
                "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('losing-card','correct','2026-09-20',1,7)"
            )
        issue = next(item for item in _integrity(client)["issues"] if item["kind"] == "multiple_responses")
        response = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": issue["signature"], "selected_move_uci": "d2d4"},
        )
        assert response.status_code == 202
        _wait_for_repair(client, response.json()["task_id"])
        with database.connection() as db:
            card = db.execute("SELECT archived,state FROM cards WHERE id='losing-card'").fetchone()
            assert card["archived"] == 1
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='losing-card'").fetchone()[0] == 1


def test_guided_repair_never_auto_selects_a_move(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5"])
            _line(db, "two", "rep", ["d2d4", "d7d5"])
        issue = _integrity(client)["issues"][0]
        response = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": issue["signature"]},
        )
        assert response.status_code == 422
        assert not any(
            task["kind"] == "integrity_repair"
            for task in client.get("/api/system/tasks").json()["tasks"]
        )


def test_repair_worker_holds_no_sqlite_connection_during_chess_traversal(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5"])
            _line(db, "two", "rep", ["d2d4", "d7d5"])
        issue = _integrity(client)["issues"][0]

        connection_open = False
        original_read_connection = integrity_service.read_connection
        original_board = integrity_service.chess.Board

        @contextmanager
        def observed_read_connection():
            nonlocal connection_open
            with original_read_connection() as connection:
                connection_open = True
                try:
                    yield connection
                finally:
                    connection_open = False

        def observed_board(*args, **kwargs):
            assert connection_open is False
            return original_board(*args, **kwargs)

        monkeypatch.setattr(integrity_service, "read_connection", observed_read_connection)
        monkeypatch.setattr(integrity_service.chess, "Board", observed_board)
        prepared = integrity_service.prepare_issue_resolution(
            "rep", issue["id"], issue["signature"], "d2d4"
        )
        assert prepared["changed_lines"]


def test_integrity_block_publication_is_generation_guarded(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        now = "2026-09-21T00:00:00+00:00"
        with database.connection() as connection:
            _line(connection, "line", "rep", ["e2e4"])
            connection.execute(
                """INSERT INTO cards(
                       id,repertoire_id,kind,start_fen,moves_json,due_date,
                       content_type,trained_color
                   ) VALUES('guarded','rep','prefix',?,'["e2e4"]',
                            '2026-09-20','opening','white')""",
                (START,),
            )
            connection.execute(
                """INSERT INTO repertoire_integrity_issues(
                       id,repertoire_id,kind,fen_key,fen,trained_color,signature,
                       moves_json,sources_json,created_at,updated_at
                   ) VALUES('guarded-issue','rep','missing_response','guarded-position',?,
                            'white','guarded-signature','[]',?, ?, ?)""",
                (
                    START,
                    json.dumps([{"type": "card", "id": "guarded"}]),
                    now,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO repertoire_integrity_card_blocks(
                       repertoire_id,card_id,issue_id,scan_generation,published_at
                   ) VALUES('rep','guarded','guarded-issue','current',?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO repertoire_integrity_jobs(
                       repertoire_id,run_id,status,source_offset,total_sources,updated_at
                   ) VALUES('rep','current','finalizing',1,1,?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO repertoire_integrity_source_runs(
                       run_id,source_offset,observations_json,invalid_json
                   ) VALUES('stale',0,'[]','[]')"""
            )

        integrity_service.execute_integrity_slice(
            {"repertoire_id": "rep", "run_id": "stale", "status": "finalizing"}
        )

        with database.read_connection() as connection:
            published = connection.execute(
                "SELECT scan_generation FROM repertoire_integrity_card_blocks WHERE card_id='guarded'"
            ).fetchone()
        assert published["scan_generation"] == "current"
