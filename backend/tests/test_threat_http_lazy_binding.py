"""Old clients receive a permanent answer before dispatch or database admission."""
import pytest
from fastapi.testclient import TestClient
from app import main


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/defensive-threats/candidates"),
    ("POST", "/api/games/analysis/repair-provenance"),
    ("POST", "/api/games/analysis/position/claim"),
    ("POST", "/api/games/provider/game/analysis"),
    ("POST", "/api/games/provider/game/defensive-threats/refresh"),
    ("POST", "/api/defense-exercises/one/attempt"),
    ("GET", "/api/discoveries/one/recommendations"),
    ("POST", "/api/discoveries/one/accept"),
])
def test_retired_analysis_paths_bypass_work_admission(monkeypatch, method, path):
    def forbidden():
        pytest.fail("retired route reached database admission")
    monkeypatch.setattr(main.postgres_store, "configured", forbidden)
    response = TestClient(main.app).request(method, path)
    assert response.status_code == 410
