import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tempo_learning_runtime import api, gateway
from tempo_learning_runtime.config import Configuration


def test_endgame_routes_and_command_are_retired():
    credential = "x" * 40
    app = api.create_app(Configuration("removed", "unused", "unused", credential, maintenance=False))
    client = TestClient(app, base_url="http://localhost", headers={"Authorization": "Bearer " + credential})
    assert client.get("/api/endgames/templates").status_code == 404
    assert client.post("/api/endgames/probe", json={}).status_code == 404
    assert client.post("/api/endgames/templates/saved/attempt", headers={"Idempotency-Key": "old"}).status_code == 404
    assert "endgames.attempt.create" not in app.state.command_registry
    with pytest.raises(HTTPException) as error:
        gateway.dispatch("endgames.attempt.create", {}, "previously-saved-operation")
    assert error.value.status_code == 422


def test_saved_published_material_validation_remains_without_calculation():
    from tempo_learning_runtime import endgames
    assert endgames.normalized_material("Q & K") == "KQ"
    with pytest.raises(ValueError):
        endgames.normalized_material("KX")
    assert not hasattr(endgames, "generate_position")
    assert not hasattr(endgames, "category_for_player")
