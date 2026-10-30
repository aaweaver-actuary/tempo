"""Reader-profile Game Review dispatch and configuration write validation.

No provider process, database or fixture service is required by these cases.
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


def test_repertoire_snapshot_connection_is_read_only_without_short_deadline(monkeypatch):
    from contextlib import contextmanager
    import psycopg
    from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections

    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("closed")

        @contextmanager
        def transaction(self):
            yield

        def execute(self, statement):
            calls.append(statement)

    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: Connection())
    connections = DedicatedGamesConnections("unused")
    with connections(repertoire_snapshot=True):
        assert calls[0] == "SET TRANSACTION READ ONLY"
        assert "set_config('transaction_timeout','0'" in calls[1]
        assert "set_config('statement_timeout','0'" in calls[1]
        assert "set_config('lock_timeout','100ms'" in calls[1]
    assert calls[-1] == "closed"
    calls.clear()
    with connections():
        assert "set_config('transaction_timeout','250ms'" in calls[0]
        assert "set_config('statement_timeout','250ms'" in calls[0]
        assert "SET TRANSACTION READ ONLY" not in calls
    assert calls[-1] == "closed"


@pytest.mark.parametrize("count", [1, 10, 17])
def test_reader_http_dispatches_original_configure_and_run_identity_without_writer(monkeypatch, count):
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
        body["configuration"]["repertoire_ids"] = [f"repertoire-{index}" for index in range(count)]
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


@pytest.fixture
def configuration_writer(monkeypatch):
    """Exercise the actual command handler/owner with only a semantic fake store."""
    from app.games.feedback.review import GameReview
    from app.product_composition import game_review_commands

    class ConfigurationStore:
        def __init__(self):
            self.saved = None
            self.writes = []

        def configuration(self, workspace, provider):
            return self.saved

        def configure(self, value, expected_revision):
            self.writes.append((value, expected_revision))
            self.saved = value
            return value

    store = ConfigurationStore()
    def forbidden(*args, **kwargs):
        pytest.fail("configuration started acquisition or delivery")
    owner = GameReview(workspace="legacy", store=store, learning=None,
        build_job=forbidden, deliver=forbidden, now=forbidden)
    monkeypatch.setattr(game_review_commands, "command_review", lambda database: owner)
    return store, owner, lambda payload: game_review_commands.configure(None, payload)


def write_request(*, end_at, interval=300):
    value = configuration().model_dump(mode="json")
    value["window"]["end_at"] = end_at
    value["interval_seconds"] = interval
    return dict(configuration=value, expected_revision=0)


@pytest.mark.parametrize("count", [10, 17])
def test_configuration_and_acquisition_job_preserve_all_selected_repertoires(configuration_writer, count):
    from app.product_composition.game_review import configured_job

    store, _, write = configuration_writer
    request = write_request(end_at="2026-10-26T00:00:00Z")
    selected = [f"repertoire-{index}" for index in range(count)]
    request["configuration"]["repertoire_ids"] = selected
    assert write(request)["repertoire_ids"] == selected
    assert list(store.saved.repertoire_ids) == selected
    job = configured_job(store.saved, "all-selected-job")
    assert list(job.repertoire_ids) == selected
    assert type(job).model_validate_json(job.model_dump_json()).repertoire_ids == tuple(selected)


@pytest.mark.parametrize("end_at", ["2026-10-25T00:01:00Z", "2026-10-26T00:00:00Z"])
def test_ongoing_seed_width_boundaries_save_without_provider_work(configuration_writer, end_at):
    store, _, write = configuration_writer
    request = write_request(end_at=end_at)
    assert write(request) == request["configuration"]
    assert store.writes == [(SyncConfiguration.model_validate_json(json.dumps(request["configuration"])), 0)]


@pytest.mark.parametrize("end_at", ["2026-10-25T00:00:59.999Z", "2026-10-26T00:00:00.001Z"])
def test_incompatible_ongoing_seed_rejected_at_command_write_before_store(configuration_writer, end_at):
    from fastapi import HTTPException
    store, _, write = configuration_writer
    # Still readable: the new policy must not live in deserialization.
    request = write_request(end_at=end_at)
    SyncConfiguration.model_validate_json(json.dumps(request["configuration"]))
    with pytest.raises(HTTPException) as rejected:
        write(request)
    assert rejected.value.status_code == 422
    assert "60–86400 seconds" in rejected.value.detail
    assert "Historical/manual import" in rejected.value.detail
    assert store.writes == []


def test_wide_manual_history_preserves_exact_times_at_command_write(configuration_writer):
    store, _, write = configuration_writer
    request = write_request(end_at="2027-01-25T12:34:56.789Z", interval=None)
    request["configuration"]["window"]["start_at"] = "2026-10-25T00:00:12.345Z"
    assert write(request) == request["configuration"]
    assert len(store.writes) == 1


def test_old_incompatible_seed_remains_readable_and_can_be_corrected(configuration_writer):
    store, owner, write = configuration_writer
    old = write_request(end_at="2027-01-25T00:00:00Z")["configuration"]
    old["revision"] = 7
    store.saved = SyncConfiguration.model_validate_json(json.dumps(old))
    assert owner.configuration("lichess").model_dump(mode="json") == old
    app = FastAPI(); app.include_router(create_game_review_router(owner))
    with TestClient(app) as client:
        result = client.get("/api/product/game-review/configuration/lichess")
        assert result.status_code == 200 and result.json() == old
    corrected = write_request(end_at="2026-10-25T01:00:00Z")
    corrected["expected_revision"] = 7
    corrected["configuration"]["revision"] = 8
    assert write(corrected) == corrected["configuration"]
    assert store.writes[0][1] == 7


@pytest.mark.parametrize("field,value", [
    ("window", dict(start_at="2026-10-26T00:00:00Z", end_at="2026-10-25T00:00:00Z", precision="second")),
    ("window", dict(start_at="2026-02-30T00:00:00Z", end_at="2026-10-25T00:00:00Z", precision="second")),
    ("window", dict(start_at="2026-10-25T00:00:00", end_at="2026-10-26T00:00:00Z", precision="second")),
    ("account", " "), ("repertoire_ids", []), ("speeds", []), ("speeds", ["unsupported"]),
    ("rated", "rated"), ("interval_seconds", 59), ("interval_seconds", 86401), ("interval_seconds", 60.5),
])
def test_existing_configuration_contract_rejects_invalid_explicit_choices_without_write(configuration_writer, field, value):
    from pydantic import ValidationError
    store, _, write = configuration_writer
    request = write_request(end_at="2026-10-25T01:00:00Z")
    request["configuration"][field] = value
    with pytest.raises(ValidationError):
        write(request)
    assert store.writes == []


def test_chesscom_casual_only_remains_unsupported_at_write(configuration_writer):
    from pydantic import ValidationError
    store, _, write = configuration_writer
    request = write_request(end_at="2026-10-25T01:00:00Z")
    request["configuration"].update(provider="chesscom", rated=False)
    with pytest.raises(ValidationError, match="casual-only is unavailable"):
        write(request)
    assert store.writes == []
