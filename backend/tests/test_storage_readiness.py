from fastapi.testclient import TestClient
from app import database
from app.main import app


def test_unavailable_database_is_actionable_and_never_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app, raise_server_exceptions=False) as client:
        original_settings = client.get("/api/settings").json()
        original_path = database.DB_PATH
        monkeypatch.setattr(database, "DB_PATH", tmp_path)
        for endpoint in ("health", "settings", "queue/today", "games/summary"):
            response = client.get(f"/api/{endpoint}")
            assert response.status_code == 503
            assert "database" in response.json()["detail"].lower()
            assert "mount" in response.json()["detail"].lower()
        monkeypatch.setattr(database, "DB_PATH", original_path)
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/settings").json() == original_settings
