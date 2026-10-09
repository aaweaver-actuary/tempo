"""Issue #107: publication fences, bounded execution and read-only API."""
from contextlib import contextmanager, nullcontext
from datetime import timedelta
import json
import threading
from types import SimpleNamespace

import psycopg
from fastapi.testclient import TestClient
import pytest

from app import main
from app.services import postgres_next_opponent as service
from app.services.next_opponent_profile import METHOD_VERSION, build_profile
from test_next_opponent_profile import CUTOFF, game


def cursor(row=None, rows=()):
    return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: rows, rowcount=1)


class Database:
    def __init__(self):
        self.account = "alice"
        self.state = {"input_generation": 1, "published_generation": None,
                      "published_method": None, "profile_json": None, "published_at": None,
                      "next_evidence_at": None}
        self.task = None
        self.sync = {"username": "alice", "status": "idle", "last_success_at": CUTOFF.isoformat()}
        self.statements = []

    def execute_native(self, statement, parameters=()):
        if "AS next_evidence_at" in statement:
            return cursor()
        return cursor(rows=[game()] if "LIMIT %s" in statement else [], row=game(speed=parameters[1]) if "LIMIT 1" in statement else None)

    def execute(self, statement, parameters=()):
        self.statements.append(statement)
        if "FROM settings" in statement:
            return cursor({"lichess_username": self.account})
        if "FROM next_opponent_accounts" in statement:
            if "AS future_evidence_due" in statement:
                return cursor({**self.state, "future_evidence_due": self.state["next_evidence_at"] is not None
                               and self.state["next_evidence_at"] <= parameters[0]})
            return cursor(self.state)
        if "FROM background_tasks" in statement:
            return cursor(self.task)
        if "FROM game_sync_state" in statement:
            return cursor(self.sync)
        if statement.startswith("UPDATE next_opponent_accounts"):
            self.state.update(published_generation=parameters[0], published_method=parameters[1],
                              next_evidence_at=parameters[3])
        if statement.startswith("INSERT INTO next_opponent_snapshots"):
            self.state.update(profile_json=parameters[3], published_at=parameters[4])
        return cursor()


def task():
    return {"id": "task", "lease_token": "current", "payload": {
        "account": "alice", "input_generation": 1, "method_version": METHOD_VERSION}}


def test_issue107_profile_refresh_coalesces_identical_intents_and_skips_published_retry(monkeypatch):
    database = Database()
    intents = []
    def enqueue(database, kind, account, payload, **kwargs):
        intents.append((kind, account, payload))
        database.task = {"state": "queued", "payload_json": json.dumps(payload)}
    monkeypatch.setattr(service, "enqueue_task_in_transaction", enqueue)
    assert service.request_profile_refresh(database)
    assert not service.request_profile_refresh(database)
    database.state.update(published_generation=1, published_method=METHOD_VERSION)
    assert not service.request_profile_refresh(database)
    database.state["input_generation"] = 2
    assert service.request_profile_refresh(database)
    assert len(intents) == 2


def test_pr116_future_evidence_refresh_waits_until_due_and_coalesces_same_generation(monkeypatch):
    database = Database()
    eligible_at = CUTOFF + timedelta(days=1)
    database.state.update(published_generation=1, published_method=METHOD_VERSION, next_evidence_at=eligible_at)
    clock = {"now": CUTOFF}
    monkeypatch.setattr(service, "_current_utc_time", lambda: clock["now"])
    intents = []
    def enqueue(database, kind, account, payload, **kwargs):
        intents.append(payload)
        database.task = {"state": "queued", "payload_json": json.dumps(payload)}
    monkeypatch.setattr(service, "enqueue_task_in_transaction", enqueue)
    assert not service.request_profile_refresh(database)
    clock["now"] = eligible_at - timedelta(microseconds=1)
    assert not service.request_profile_refresh(database)
    clock["now"] = eligible_at
    assert service.request_profile_refresh(database)
    assert not service.request_profile_refresh(database)
    assert intents == [task()["payload"]]


def test_pr116_due_future_evidence_is_pending_without_read_side_effects():
    database = Database()
    profile = build_profile("alice", [game()], as_of=CUTOFF)
    database.state.update(profile_json=profile.model_dump_json(), published_at=CUTOFF.isoformat(),
                          published_generation=1, published_method=METHOD_VERSION,
                          next_evidence_at=CUTOFF + timedelta(days=1))
    assert service.read_profile(database, now=CUTOFF).refresh_status == "idle"
    response = service.read_profile(database, now=CUTOFF + timedelta(days=1))
    assert response.refresh_status == "pending" and "inputs_pending" in response.stale_reasons
    assert response.profile == profile and response.published_at == CUTOFF.isoformat()
    assert all(statement.startswith("SELECT") for statement in database.statements)


@pytest.mark.parametrize("change", ["source", "account", "delivery", "loaded_newer", "method"])
def test_issue107_stale_source_account_and_delivery_cannot_replace_snapshot(monkeypatch, change):
    database = Database()
    profile = build_profile("alice", [game()], as_of=CUTOFF)
    claimed = task()
    if change == "source":
        database.state["input_generation"] = 2
    if change == "account":
        database.account = "bob"
    if change == "method":
        claimed["payload"]["method_version"] = "retired"
    monkeypatch.setattr(service, "lock_current_slice", lambda *_: change != "delivery")
    monkeypatch.setattr(service, "complete_task_slice_in_transaction", lambda *_: True)
    service._publish(database, claimed, 2 if change == "loaded_newer" else 1, profile, CUTOFF.isoformat())
    assert not any(statement.startswith("INSERT INTO next_opponent_snapshots") for statement in database.statements)
    assert database.state["next_evidence_at"] is None


def test_issue107_computation_closes_database_and_interruption_replays_without_partial_publication(monkeypatch):
    database = Database()
    connections = {"open": 0}
    @contextmanager
    def connection(**options):
        connections["open"] += 1
        try:
            yield database
        finally:
            connections["open"] -= 1
    monkeypatch.setattr(service, "connection", connection)
    monkeypatch.setattr(service, "background_lease", nullcontext)
    monkeypatch.setattr(service, "lock_current_slice", lambda *_: True)
    monkeypatch.setattr(service, "complete_task_slice_in_transaction", lambda *_: True)
    actual_build = service.build_profile
    def interrupted(*args, **kwargs):
        assert connections["open"] == 0
        raise RuntimeError("worker interrupted")
    monkeypatch.setattr(service, "build_profile", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        service.execute_profile_slice(task())
    assert database.state["profile_json"] is None
    def resumed(*args, **kwargs):
        assert connections["open"] == 0
        return actual_build(*args, **kwargs)
    monkeypatch.setattr(service, "build_profile", resumed)
    assert service.execute_profile_slice(task())
    assert connections["open"] == 0 and database.state["published_generation"] == 1


def test_issue107_foreground_contention_prevents_profile_database_reads(monkeypatch):
    foreground_active = threading.Event()
    admitted = threading.Event()
    read = threading.Event()
    errors = []
    @contextmanager
    def admission():
        foreground_active.set()
        assert admitted.wait(2)
        yield
    @contextmanager
    def connection(**options):
        read.set()
        yield Database()
    monkeypatch.setattr(service, "background_lease", admission)
    monkeypatch.setattr(service, "connection", connection)
    def run():
        try:
            service._load_inputs("alice", CUTOFF)
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert foreground_active.wait(2)
        assert not read.is_set()
    finally:
        admitted.set()
        worker.join(2)
    assert read.is_set()
    assert errors == []


def test_issue107_api_fixed_speed_and_freshness_do_not_mutate_snapshot_or_queue(monkeypatch):
    database = Database()
    profile = build_profile("alice", [game()], as_of=CUTOFF)
    database.state.update(profile_json=profile.model_dump_json(), published_at=CUTOFF.isoformat(),
                          published_generation=1, published_method=METHOD_VERSION)
    fixed = service.read_profile(database, "rapid", now=CUTOFF)
    assert fixed.availability == "available" and not fixed.stale
    assert [(item.speed, item.weight) for item in fixed.effective_speed_mixture] == [("rapid", 1)]
    stale = service.read_profile(database, "rapid", now=CUTOFF + timedelta(days=40))
    assert stale.stale and stale.profile == fixed.profile
    assert stale.stale_cohorts == ("rapid",)
    assert all(statement.startswith("SELECT") for statement in database.statements)
    database.sync["status"] = "error"
    assert service.read_profile(database, now=CUTOFF).refresh_status == "sync_error"
    database.task = {"state": "failed"}
    database.sync["status"] = "idle"
    assert service.read_profile(database, now=CUTOFF).refresh_status == "failed"


def test_issue107_unknown_pending_account_switch_and_unsupported_are_explicit():
    database = Database()
    database.account = ""
    assert service.read_profile(database).availability == "unknown"
    database.account = "bob"
    database.sync["username"] = "alice"
    response = service.read_profile(database)
    assert response.availability == "pending" and response.last_successful_sync_at is None
    assert "no_successful_sync" in response.stale_reasons


def test_issue107_http_contract_invalid_speed_unsupported_and_storage_error(monkeypatch):
    monkeypatch.setattr(main.postgres_store, "configured", lambda: False)
    client = TestClient(main.app)
    assert client.get("/api/games/next-opponent-profile?speed=bullet").status_code == 422
    assert client.get("/api/games/next-opponent-profile").json()["availability"] == "unsupported"
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    @contextmanager
    def unavailable():
        raise psycopg.OperationalError("private internal details")
        yield
    monkeypatch.setattr(main, "read_connection", unavailable)
    response = client.get("/api/games/next-opponent-profile")
    assert response.status_code == 503
    assert "check Tempo service status" in response.json()["detail"]
    assert "private internal details" not in response.text


def test_issue107_missing_settings_is_service_error_not_unknown_account(monkeypatch):
    database = Database()
    original_execute = database.execute
    monkeypatch.setattr(database, "execute", lambda statement, parameters=():
                        cursor() if "FROM settings" in statement else original_execute(statement, parameters))
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "read_connection", lambda: nullcontext(database))
    response = TestClient(main.app).get("/api/games/next-opponent-profile")
    assert response.status_code == 503
    assert "check Tempo service status" in response.json()["detail"]


def test_issue107_regular_worker_dispatch_completes_profile_receipt_once(monkeypatch):
    from app import tasks
    claimed = {**task(), "kind": service.TASK_KIND, "generation": 1}
    calls = []
    assert service.TASK_KIND in tasks._SUPPORTED_BACKGROUND_KINDS
    monkeypatch.setattr(tasks, "current_delivery", lambda *_: True)
    monkeypatch.setattr(tasks, "defer_paused_defensive_task", lambda *_: False)
    monkeypatch.setattr(tasks, "measure_handler", lambda *_: nullcontext())
    monkeypatch.setattr(tasks.activity_gate, "background_job", lambda *_: nullcontext())
    monkeypatch.setattr(tasks, "execute_profile_slice", lambda saved: calls.append(saved) or False)
    monkeypatch.setattr(tasks, "complete_task", lambda *_args, **_kwargs: pytest.fail("handler already owns atomic receipt"))
    assert not tasks._execute_claimed_background_slice(claimed, None)
    assert calls == [claimed]


def test_issue107_profile_http_reads_only_published_account_snapshot(monkeypatch):
    database = Database()
    profile = build_profile("alice", [game()], as_of=CUTOFF)
    database.state.update(profile_json=profile.model_dump_json(), published_at=CUTOFF.isoformat(),
                          published_generation=1, published_method=METHOD_VERSION)
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "read_connection", lambda: nullcontext(database))
    response = TestClient(main.app).get("/api/games/next-opponent-profile?speed=rapid")
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["version"] == profile.version
    assert payload["effective_speed_mixture"] == [{"speed": "rapid", "weight": 1.0}]
    assert all(statement.startswith("SELECT") for statement in database.statements)


def test_issue107_unsupported_speed_only_does_not_fabricate_supported_probability():
    database = Database()
    profile = build_profile("alice", [game(speed="bullet")], as_of=CUTOFF)
    database.state.update(profile_json=profile.model_dump_json(), published_at=CUTOFF.isoformat(),
                          published_generation=1, published_method=METHOD_VERSION)
    response = service.read_profile(database, now=CUTOFF)
    assert response.availability == "unsupported"
    assert response.profile.unsupported_speed_mass == 1
    assert [(item.speed, item.weight) for item in response.effective_speed_mixture] == [("bullet", 1)]


def test_issue107_maintenance_profile_proof_configures_disposable_redis(monkeypatch):
    from scripts import check_postgres_next_opponent as proof
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    monkeypatch.setattr(proof.sys, "argv", ["proof", "--resume"])
    observed = []
    monkeypatch.setattr(proof, "child", lambda mode: observed.append((mode, proof.os.environ["TEMPO_REDIS_URL"])))
    from unittest.mock import patch
    with patch.dict(proof.os.environ):
        proof.main()
        assert observed == [("--resume", "redis://redis:6379/0")]
    assert "TEMPO_REDIS_URL" not in proof.os.environ
