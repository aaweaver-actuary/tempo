from fastapi.testclient import TestClient
from app import database
from app.main import app

def test_tactic_progress_returns_distinct_discovered_ids_including_legacy_clean_records(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            for number in range(24):
                db.execute("INSERT INTO tactic_progress(puzzle_id,deck_id,clean_pass_at) VALUES(?,?,?)", (f"legacy-{number}", "hangingPiece-easy", "2026-09-16T12:00:00Z"))
        progress = client.get("/api/tactics/progress").json()["hangingPiece:easy"]
        assert progress["index"] == 24
        assert len(progress["discoveredIds"]) == 24
        assert set(progress["discoveredIds"]) == set(progress["cleanIds"])
