"""Defensive engine claims and callbacks use lease-fenced PostgreSQL receipts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import command_dispatch, main, postgres_store, threat_analysis_commands
from app.models import GameAnalysisLeaseRequest, ThreatAnalysisFailureRequest, ThreatAnalysisSubmission


class Cursor:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = rows

    def fetchone(self):
        return self.row

    def __iter__(self):
        return iter(self.rows)


def test_postgres_threat_claim_reclaims_one_lease_and_returns_claimed_request(monkeypatch):
    statements = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))
            if statement.startswith("SELECT request.id"):
                return Cursor({"id": "request-one", "request_json": '{"depth":14}'})
            return Cursor()

    result = threat_analysis_commands.claim_threat_analysis(Database(), {})
    assert result["job"]["id"] == "request-one"
    assert result["job"]["request"] == {"depth": 14}
    assert result["job"]["lease_id"]
    assert "LIMIT 1 FOR UPDATE SKIP LOCKED" in statements[0][0]
    assert "FOR UPDATE OF request SKIP LOCKED" in statements[1][0]


def test_postgres_threat_claim_checks_sparse_priorities_before_ordered_queue():
    statements = []
    request_queries = 0

    class Database:
        def execute_native(self, statement, parameters=()):
            nonlocal request_queries
            statements.append(statement)
            if statement.startswith("SELECT request.id"):
                request_queries += 1
                if request_queries == 2:
                    return Cursor({"id": "ordinary-request", "request_json": "{}"})
            return Cursor()

    result = threat_analysis_commands.claim_threat_analysis(Database(), {})
    assert result["job"]["id"] == "ordinary-request"
    assert "WHERE role='attempt'" in statements[1]
    assert "SELECT 1 FROM background_activity" in statements[2]
    assert "ORDER BY request.created_at,request.id" in statements[3]
    assert "LIMIT 1 OFFSET 0" in statements[3]


def test_postgres_threat_report_validates_lease_and_queues_candidates_atomically(monkeypatch):
    state = {"lease_id": "current", "status": "leased"}
    writes = []
    validations = []
    enqueued = []

    class Database:
        def execute_native(self, statement, parameters=()):
            if statement.startswith("SELECT state,lease_id,request_json"):
                return Cursor({"state": state["status"], "lease_id": state["lease_id"],
                               "request_json": "{}"})
            if statement.startswith("SELECT DISTINCT candidate_id"):
                return Cursor(rows=[("candidate-one",), ("candidate-two",)])
            writes.append((statement, parameters))
            return Cursor()

    monkeypatch.setattr(threat_analysis_commands, "report_from_json",
                        lambda _raw: SimpleNamespace(
                            request=SimpleNamespace(request_id="request-one")))
    monkeypatch.setattr(threat_analysis_commands, "_request_from_json", lambda _raw: object())
    monkeypatch.setattr(threat_analysis_commands, "validate_analysis_report",
                        lambda *_args: validations.append(True))
    monkeypatch.setattr(threat_analysis_commands,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        enqueued.append((kind, key, payload, priority)))
    payload = {"request_id": "request-one", "lease_id": "current", "report": {}}
    assert threat_analysis_commands.submit_threat_report(Database(), payload) == {
        "status": "complete", "candidate_count": 2,
    }
    assert validations == [True]
    assert len(writes) == 2
    assert sum("INSERT INTO background_metric_buckets" in statement for statement, _ in writes) == 1
    assert [item[1] for item in enqueued] == ["candidate-one", "candidate-two"]

    state["lease_id"] = "replacement"
    with pytest.raises(HTTPException) as error:
        threat_analysis_commands.submit_threat_report(Database(), payload)
    assert error.value.status_code == 409
    assert len(writes) == 2
    assert sum("INSERT INTO background_metric_buckets" in statement for statement, _ in writes) == 1
    assert len(enqueued) == 2


def test_postgres_threat_failure_release_retry_preserve_http_contract():
    class Database:
        def __init__(self, result):
            self.result = result

        def execute_native(self, _statement, _parameters=()):
            return Cursor(self.result)

    payload = {"request_id": "request-one", "lease_id": "current", "error": "timeout"}
    assert threat_analysis_commands.fail_threat_analysis(Database(("queued",)), payload) == {
        "status": "retrying",
    }
    assert threat_analysis_commands.fail_threat_analysis(Database(("failed",)), payload) == {
        "status": "failed",
    }
    with pytest.raises(HTTPException):
        threat_analysis_commands.fail_threat_analysis(Database(None), payload)
    assert threat_analysis_commands.release_threat_analysis(Database(("request-one",)), payload) == {
        "status": "queued",
    }
    assert threat_analysis_commands.release_threat_analysis(Database(None), payload) == {
        "status": "stale",
    }
    assert threat_analysis_commands.retry_threat_analysis(Database(("request-one",)), payload) == {
        "status": "queued",
    }
    with pytest.raises(HTTPException):
        threat_analysis_commands.retry_threat_analysis(Database(None), payload)


def test_postgres_threat_routes_reuse_engine_operation_id(monkeypatch):
    calls = []
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key, background=False:
                        calls.append((name, payload, idempotency_key, background))
                        or {"accepted": True})
    assert main.claim_defensive_threat_analysis("docker", "claim-one") == {"accepted": True}
    assert main.submit_defensive_threat_analysis(
        "request-one", ThreatAnalysisSubmission(lease_id="lease-one", report={}),
        "report-one",
    ) == {"accepted": True}
    assert main.fail_defensive_threat_analysis(
        "request-one", ThreatAnalysisFailureRequest(lease_id="lease-one", error="timeout"),
        "failure-one",
    ) == {"accepted": True}
    assert main.release_defensive_threat_analysis(
        "request-one", GameAnalysisLeaseRequest(lease_id="lease-one"),
        "release-one",
    ) == {"accepted": True}
    assert main.retry_defensive_threat_analysis("request-one", "retry-one") == {"accepted": True}
    assert [call[2] for call in calls] == [
        "claim-one", "report-one", "failure-one", "release-one", "retry-one",
    ]
    assert [call[3] for call in calls] == [True, True, True, True, False]
