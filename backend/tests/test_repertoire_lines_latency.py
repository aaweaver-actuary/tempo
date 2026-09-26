from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_repertoire_lines_read_stays_available_during_write_compatibility_contention(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        database_connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
            ("test-opening", "Test opening", "test.pgn", "2026-09-25T00:00:00Z"),
        )
        database_connection.execute(
            """INSERT INTO repertoire_lines(
                id,repertoire_id,name,trained_color,start_fen,moves_json,created_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                "test-line", "test-opening", "Test line", "white",
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                '["e2e4"]', "2026-09-25T00:00:00Z",
            ),
        )

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        database.write_compatibility_lock.acquire()
        try:
            pending_response = executor.submit(client.get, "/api/repertoire/lines")
            response = pending_response.result(timeout=2)
        finally:
            database.write_compatibility_lock.release()
        assert response.status_code == 200
        assert response.json()["lines"][0]["moves"] == ["e2e4"]
