"""Reader HTTP dispatch and optional-worker bindings; no fixture services."""
from contextlib import contextmanager, nullcontext
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.games.feedback.http import create_game_review_router
from app.games.feedback.review import ReviewUnavailable, SyncConfiguration
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


def test_edited_configuration_stops_stale_clients_before_provider_work(monkeypatch):
    from app.product_composition import game_sync_current as current
    saved=[configuration()]
    class Configurations:
        def __init__(self,connections):pass
        def configuration(self,workspace,provider):return saved[0] if provider=="lichess" else None
    supplied={}
    def runtime(**values):
        supplied.update(values)
        return SimpleNamespace(runner=SimpleNamespace(coordinator=SimpleNamespace(deliver=lambda _:None)))
    monkeypatch.setattr(current,"PostgresReviewStore",Configurations)
    monkeypatch.setattr(current,"PostgresCanonicalGamesStore",lambda _:object())
    monkeypatch.setattr(current,"create_game_sync",runtime)
    monkeypatch.setattr(current,"create_game_review",lambda **_:object())
    monkeypatch.setattr(current,"current_sync_admission",nullcontext)
    current.create_current_game_sync(workspace="legacy",connections=object(),learning=object())
    with supplied["admission"]():pass
    saved[0]=saved[0].model_copy(update={"revision":2,"account":"Different"})
    with pytest.raises(ReviewUnavailable,match="sync_configuration_changed_restart_worker"):
        with supplied["admission"]():pytest.fail("stale acquisition policy was used")


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


def test_provider_root_commits_public_intake_before_ack_and_never_refreshes_queue(monkeypatch):
    from app.product_composition import provider_priority as root
    from app.product_composition import game_sync_current
    events=[]
    @contextmanager
    def writer():
        events.append("open");yield "borrowed";events.append("commit")
    connections=SimpleNamespace(writer=writer)
    captured={"game":"exact retained source"};snapshot=object()
    command=SimpleNamespace(decision=SimpleNamespace(repertoire_id="white"),
                            key=SimpleNamespace(game_id="canonical",occurrence_ply=3))
    def capture(*args):events.append(("capture",args));return captured
    monkeypatch.setattr(game_sync_current,"_require_current_sync_controls",lambda:events.append("controls"))
    monkeypatch.setattr(root,"retained_canonical_game",lambda value: "canonical" if value is captured else None)
    monkeypatch.setattr(root,"project_capture",lambda value,**_:snapshot if value is captured else None)
    receipt=object()
    def receive(database,**values):
        assert database=="borrowed" and values["command"] is command
        assert values["snapshot"] is snapshot and values["capture"] is captured
        assert values["operation_stopped"]() is False
        events.append("accepted_pending");return receipt
    monkeypatch.setattr(root,"receive_admitted_encounter",receive)
    intake=root.CurrentProviderPriorityIntake(connections=connections,
        clock=SimpleNamespace(utc_now=lambda:"2026-10-26T12:00:00Z"),
        operation_stopped=lambda:False,capture=capture)
    assert intake.accept_encounter(command) is receipt
    assert events==["controls",("capture",("white","canonical",(2,))),"open","accepted_pending","commit"]
