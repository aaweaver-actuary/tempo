"""Issue 171: transient claim reads retain truthful HTTP and operation identity."""

from types import SimpleNamespace

from fastapi.testclient import TestClient
from psycopg.errors import LockNotAvailable, TransactionTimeout, UndefinedTable
from psycopg_pool import PoolTimeout
import pytest

from app import command_dispatch, game_analysis_commands, main
from app.command_gateway import CommandConflict
from app.services import position_claim_diagnostics as diagnostics
from app.services.redis_admission_gate import BackgroundAdmissionDeferred


@pytest.fixture
def claim_route(monkeypatch):
    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    monkeypatch.delenv("TEMPO_FOREGROUND_ACTIVITY_URL", raising=False)
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    observations = {"preparations": 0, "deliveries": []}

    def prepare(*_args):
        observations["preparations"] += 1
        return {"game_id": "synthetic-game", "analysis_version": 1, "kind": "search"}

    monkeypatch.setattr(game_analysis_commands, "prepare_position_claim", prepare)
    monkeypatch.setattr(main, "read_operation", lambda *_args, **_options: {"state": "unknown"})
    monkeypatch.setattr(command_dispatch.celery_app, "send_task",
                        lambda *_args, **options: observations["deliveries"].append(options)
                        or SimpleNamespace(get=lambda **_options: None))
    monkeypatch.setattr(command_dispatch, "read_operation",
                        lambda *_args, **_options: {"state": "complete", "response": {"job": None}})
    client = TestClient(main.app, raise_server_exceptions=False)

    def post():
        return client.post("/api/games/analysis/position/claim", headers={
            "X-Tempo-Engine-Worker": "docker", "X-Tempo-Work-Class": "background",
            "Idempotency-Key": "synthetic-issue171-operation",
        })

    return SimpleNamespace(post=post, observations=observations)


@pytest.mark.parametrize("failure_stage", ["preparation", "prior_receipt", "published_receipt"])
def test_issue171_position_claim_transaction_timeout_returns_retryable_503(claim_route, monkeypatch,
                                                                         failure_stage):
    def expired(*_args, **_options):
        raise TransactionTimeout("synthetic transaction budget expired")

    if failure_stage == "preparation":
        monkeypatch.setattr(game_analysis_commands, "prepare_position_claim", expired)
    elif failure_stage == "prior_receipt":
        monkeypatch.setattr(main, "read_operation", expired)
    else:
        monkeypatch.setattr(command_dispatch, "read_operation", expired)
    response = claim_route.post()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert "same Idempotency-Key" in response.json()["detail"]
    assert "job" not in response.json()
    assert len(claim_route.observations["deliveries"]) == (1 if failure_stage == "published_receipt" else 0)
    if failure_stage == "published_receipt":
        assert claim_route.observations["deliveries"][0]["args"][0] == "synthetic-issue171-operation"


def test_issue171_position_claim_foreground_denial_retains_503_without_dispatch(claim_route, monkeypatch):
    def denied(*_args):
        raise BackgroundAdmissionDeferred("Waiting for foreground activity")
    monkeypatch.setattr(game_analysis_commands, "prepare_position_claim", denied)
    response = claim_route.post()
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"
    assert response.json() == {"detail": "Waiting for foreground activity"}
    assert not claim_route.observations["deliveries"]


@pytest.mark.parametrize("error_class", [LockNotAvailable, PoolTimeout])
@pytest.mark.parametrize("failure_stage", ["preparation", "prior_receipt", "published_receipt"])
def test_issue171_position_claim_lock_and_pool_timeouts_are_retryable(claim_route, monkeypatch,
                                                                    failure_stage, error_class):
    def unavailable(*_args, **_options):
        raise error_class("synthetic database contention")
    owner, attribute = {
        "preparation": (game_analysis_commands, "prepare_position_claim"),
        "prior_receipt": (main, "read_operation"),
        "published_receipt": (command_dispatch, "read_operation"),
    }[failure_stage]
    monkeypatch.setattr(owner, attribute, unavailable)
    response = claim_route.post()
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"
    assert "same Idempotency-Key" in response.json()["detail"]
    assert len(claim_route.observations["deliveries"]) == (1 if failure_stage == "published_receipt" else 0)


def test_issue171_position_claim_conflicting_identity_remains_409(claim_route, monkeypatch):
    def conflicting(*_args, **_options):
        raise CommandConflict("Operation ID was already used for another request")
    monkeypatch.setattr(main, "read_operation", conflicting)
    assert claim_route.post().status_code == 409
    assert claim_route.observations == {"preparations": 0, "deliveries": []}


def test_issue171_position_claim_unexpected_sql_failure_remains_500(claim_route, monkeypatch):
    def broken(*_args):
        raise UndefinedTable("synthetic missing relation")
    monkeypatch.setattr(game_analysis_commands, "prepare_position_claim", broken)
    response = claim_route.post()
    assert response.status_code == 500 and 'Retry-After' not in response.headers
    assert not claim_route.observations['deliveries']


@pytest.mark.parametrize("state,status", [("complete", 200), ("failed", 500), ("retrying", 202)])
def test_issue171_position_claim_replay_preserves_existing_receipt_semantics(claim_route, monkeypatch,
                                                                          state, status):
    original_response = {"job": {"lease_id": "original-lease", "kind": "search"}}
    monkeypatch.setattr(main, "read_operation", lambda *_args, **_options: {
        "state": state, "response": original_response,
        "error": {"status_code": 500, "message": "definitive failure"},
    })
    response = claim_route.post()
    assert response.status_code == status
    assert claim_route.observations == {"preparations": 0, "deliveries": []}
    if state == "complete":
        assert response.json() == original_response
    elif state == "failed":
        assert response.json() == {"detail": "definitive failure"}
    else:
        assert response.headers["Location"].endswith("synthetic-issue171-operation")


def test_issue171_position_claim_diagnostics_are_opt_in_redacted_and_bounded(monkeypatch):
    import json
    records = []
    monkeypatch.setattr(diagnostics._LOGGER, "info", records.append)
    monkeypatch.delenv("TEMPO_POSITION_CLAIM_DIAGNOSTICS", raising=False)
    with diagnostics.trace_position_claim("private-operation", "api"):
        diagnostics.record_stage("candidate_read", 0.01)
    assert not records and diagnostics.cursor_options() == {}
    monkeypatch.setenv("TEMPO_POSITION_CLAIM_DIAGNOSTICS", "1")
    with diagnostics.trace_position_claim("private-operation", "api"):
        diagnostics.record_attempt_owner("private-attempt-token", 2)
        diagnostics.record_stage("receipt_observation", 0, receipt_state="retrying", payload="private-record")
        for _ in range(diagnostics.MAX_TRACE_EVENTS + 2):
            diagnostics.record_stage("candidate_read", 0.01, TransactionTimeout("private SQL values"))
        assert not records
    record = json.loads(records[0])
    assert len(record["events"]) == diagnostics.MAX_TRACE_EVENTS and record["omitted_events"] == 4
    assert "private-operation" not in records[0] and "private SQL values" not in records[0]
    assert 'private-attempt-token' not in records[0] and 'private-record' not in records[0]
    assert record['events'][0]['attempt_number'] == 2
    assert record['events'][1]['receipt_state'] == 'retrying'
    assert record["events"][2]["sqlstate"] == "25P04"
    assert diagnostics._current_trace.get() is None


@pytest.mark.parametrize("failure_site", ["record", "emit"])
def test_issue171_position_claim_diagnostics_preserve_original_failure(monkeypatch, failure_site):
    monkeypatch.setenv("TEMPO_POSITION_CLAIM_DIAGNOSTICS", "1")
    original_error = TransactionTimeout("original failure")
    def broken(*_args, **_options):
        raise OSError("diagnostic sink unavailable")
    monkeypatch.setattr(diagnostics.ClaimTrace if failure_site == "record" else diagnostics._LOGGER,
                        "record" if failure_site == "record" else "info", broken)
    with pytest.raises(TransactionTimeout) as raised:
        with diagnostics.trace_position_claim("private-operation", "api"), diagnostics.claim_stage("candidate_read"):
            raise original_error
    assert raised.value is original_error and diagnostics._current_trace.get() is None


def test_issue171_position_claim_acquisition_failure_is_traced_before_connection_exists(monkeypatch):
    from contextlib import contextmanager
    import json
    from psycopg_pool import PoolTimeout
    from app import postgres_store
    records = []
    @contextmanager
    def unavailable(**_options):
        raise PoolTimeout("private DSN")
        yield
    monkeypatch.setattr(postgres_store, "_pool", lambda _read_only: SimpleNamespace(connection=unavailable))
    monkeypatch.setenv("TEMPO_POSITION_CLAIM_DIAGNOSTICS", "1")
    monkeypatch.setattr(diagnostics._LOGGER, "info", records.append)
    with pytest.raises(PoolTimeout):
        with diagnostics.trace_position_claim("private-operation", "api"), postgres_store.connection(background=True):
            pytest.fail("unavailable pool yielded a connection")
    events = json.loads(records[0])["events"]
    assert len(events) == 1 and events[0]["stage"] == "connection_acquisition"
    assert events[0]["exception_class"] == "PoolTimeout" and "private DSN" not in records[0]


def test_issue171_position_claim_diagnostics_emit_after_database_release(monkeypatch):
    from contextlib import contextmanager
    from app import postgres_store
    released = False
    @contextmanager
    def reservation(**_options):
        nonlocal released
        try:
            yield SimpleNamespace(info=SimpleNamespace(backend_pid=42))
        finally:
            released = True
    monkeypatch.setattr(postgres_store, "_pool", lambda _read_only: SimpleNamespace(connection=reservation))
    monkeypatch.setattr(postgres_store.PostgresConnection, "flush_background_metrics", lambda _self: None)
    monkeypatch.setenv("TEMPO_POSITION_CLAIM_DIAGNOSTICS", "1")
    emitted = []
    def emit(record):
        assert released
        emitted.append(record)
    monkeypatch.setattr(diagnostics._LOGGER, "info", emit)
    with diagnostics.trace_position_claim("private-operation", "api"):
        with postgres_store.connection():
            diagnostics.record_stage("candidate_read", 0.01)
            assert not emitted
        assert released and not emitted
    assert len(emitted) == 1
