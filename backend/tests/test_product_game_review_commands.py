"""Reader-profile Game Review dispatch; extracted unchanged from #558.

No provider process or fixture service is required by these two HTTP cases.
"""
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.games.feedback.http import create_game_review_router
from app.games.feedback.review import SyncConfiguration
from app.product_composition.game_review import CurrentGameReviewOperations


def configuration():
    return SyncConfiguration.model_validate_json(json.dumps(dict(workspace_id="legacy", provider="lichess",
        account="Learner", window=dict(start_at="2026-10-25T00:00:00Z",end_at="2026-10-26T00:00:00Z",
        precision="second"), repertoire_ids=["white"], speeds=["rapid"], rated=True,
        interval_seconds=60, revision=1)))


def test_reader_http_dispatches_original_configure_and_run_identity_without_writer(monkeypatch):
    from app import command_dispatch
    monkeypatch.delenv("TEMPO_DATABASE_WRITE_URL", raising=False)
    operations = CurrentGameReviewOperations()
    monkeypatch.setattr(operations, "_operations", lambda **_: pytest.fail("HTTP opened a writer"))
    calls = []
    def dispatch(name, payload, *, idempotency_key):
        calls.append((name,payload,idempotency_key))
        return JSONResponse(status_code=202,content=dict(operation_id=idempotency_key,state="queued"))
    monkeypatch.setattr(command_dispatch,"dispatch_command",dispatch)
    app=FastAPI();app.include_router(create_game_review_router(operations))
    with TestClient(app) as client:
        body=dict(configuration=configuration().model_dump(mode="json"),expected_revision=0)
        for _ in range(2):
            response=client.post("/api/product/game-review/configuration",json=body,
                                 headers={"Idempotency-Key":"same-save"})
            assert response.status_code==202 and response.json()["operation_id"]=="same-save"
        response=client.post("/api/product/game-review/sync",json=dict(provider="lichess",operation_id="same-run"))
        assert response.status_code==202
    assert calls[0]==calls[1]==("product_games.configure",body,"same-save")
    assert calls[2]==("product_games.sync",dict(provider="lichess",operation_id="same-run"),"same-run")


def test_configuration_receipt_json_arrays_keep_strict_immutable_http_response(monkeypatch):
    from app import command_dispatch
    expected = configuration().model_dump(mode="json")
    monkeypatch.setattr(command_dispatch,"dispatch_command",lambda *args,**kwargs:expected)
    monkeypatch.delenv("TEMPO_DATABASE_WRITE_URL",raising=False)
    app=FastAPI();app.include_router(create_game_review_router(CurrentGameReviewOperations()))
    with TestClient(app) as client:
        response=client.post("/api/product/game-review/configuration",
            json=dict(configuration=expected,expected_revision=0),headers={"Idempotency-Key":"strict-receipt"})
    assert response.status_code==200 and response.json()==expected
    parsed=SyncConfiguration.model_validate_json(response.text)
    assert parsed.repertoire_ids==("white",) and parsed.speeds==("rapid",)


