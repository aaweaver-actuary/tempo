"""Compatibility and authoritative preparation seams in the regular suite."""

from tempo_storage import postgres
from contextlib import asynccontextmanager, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest


@pytest.fixture
def checkpoint_http_envelope():
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/opening-evidence-manifest.json"
        ).read_text()
    )
    return {
        "attempt_id": "http-admission-attempt",
        "manifest": manifest,
        "origin_queue_entry_id": 101,
        "started_at": "2026-09-30T12:00:00Z",
        "study_timezone": "UTC",
    }


@pytest.fixture
def review_policy_settings(monkeypatch):
    """Give review-policy reads the two settings owned by the legacy schema."""
    from app.services import learning_review_adapter

    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("""CREATE TABLE settings (
        id INTEGER PRIMARY KEY,
        timezone TEXT NOT NULL,
        light_first_interval_days INTEGER NOT NULL
    )""")
    database.execute(
        "INSERT INTO settings (id, timezone, light_first_interval_days) VALUES (1, 'UTC', 7)"
    )

    @contextmanager
    def read_connection():
        yield database

    monkeypatch.setattr(learning_review_adapter, "read_connection", read_connection)
    try:
        yield database
    finally:
        database.close()


@pytest.mark.parametrize("background_header", [True, False])
def test_opening_checkpoint_http_dispatch_does_not_prepare_evidence_in_api(
    monkeypatch, checkpoint_http_envelope, background_header
):
    monkeypatch.setattr(postgres, "configured", lambda: True)
    from fastapi.testclient import TestClient
    from app import main, command_dispatch
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from app.services.activity_gate import activity_gate

    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)

    monkeypatch.setattr(
        postgres,
        "connection",
        lambda **options: pytest.fail(
            "Checkpoint HTTP dispatch performed an API-side evidence source read"
        ),
    )
    submitted = []

    def dispatch(command_name, payload, **options):
        assert activity_gate.in_background, (
            "Checkpoint middleware acquired a foreground lease"
        )
        submitted.append((command_name, payload, options))
        return {"operation_id": "http-checkpoint-key", "state": "pending"}

    monkeypatch.setattr(command_dispatch, "dispatch_command", dispatch)
    headers = {
        "Idempotency-Key": "http-checkpoint-key",
        **({"X-Tempo-Work-Class": "background"} if background_header else {}),
    }
    response = TestClient(main.app).post(
        "/api/opening-evidence/checkpoints",
        json=checkpoint_http_envelope,
        headers=headers,
    )
    assert response.status_code == 200
    assert submitted == [
        (
            "opening_evidence.checkpoint",
            {
                "checkpoint": OpeningEvidenceCheckpoint.model_validate(
                    checkpoint_http_envelope
                ).model_dump(mode="json")
            },
            {"idempotency_key": "http-checkpoint-key", "background": True},
        )
    ]










def test_opening_checkpoint_http_schema_rejection_precedes_dispatch(
    monkeypatch, checkpoint_http_envelope
):
    monkeypatch.setattr(postgres, "configured", lambda: True)
    from fastapi.testclient import TestClient
    from app import main, command_dispatch

    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)

    monkeypatch.setattr(
        postgres,
        "connection",
        lambda **options: pytest.fail("Invalid schema opened PostgreSQL"),
    )
    monkeypatch.setattr(
        command_dispatch,
        "dispatch_command",
        lambda *args, **options: pytest.fail("Invalid schema reached the broker"),
    )
    response = TestClient(main.app).post(
        "/api/opening-evidence/checkpoints",
        json={**checkpoint_http_envelope, "events": "invalid"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "events"]


def test_opening_checkpoint_payload_identity_ignores_historical_preparation(
    checkpoint_http_envelope,
):
    from app.command_gateway import request_digest

    payload = {"checkpoint": checkpoint_http_envelope}
    historical_payload = {**payload, "prepared_manifest": {"obsolete": True}}
    digest = request_digest("opening_evidence.checkpoint", payload)
    assert request_digest("opening_evidence.checkpoint", historical_payload) == digest
    changed_payload = {
        "checkpoint": {**checkpoint_http_envelope, "study_timezone": "America/New_York"}
    }
    assert request_digest("opening_evidence.checkpoint", changed_payload) != digest


@pytest.mark.parametrize("reconciling", [False, True])
def test_foreground_review_http_preparation_keeps_foreground_request_lease(
    monkeypatch, checkpoint_http_envelope, reconciling
):
    from tempo_storage.opening_evidence import storage

    monkeypatch.setattr(storage, "postgres_is_configured", lambda: True)
    monkeypatch.setattr(postgres, "configured", lambda: True)
    from fastapi.testclient import TestClient
    from app import main, command_dispatch
    from app.services.activity_gate import activity_gate

    manifest = checkpoint_http_envelope["manifest"]
    completion = {
        **checkpoint_http_envelope,
        "queue_entry_id": 101,
        "terminal": {
            "state": "complete",
            "final_sequence": 0,
            "ended_at": "2026-09-30T12:01:00Z",
        },
    }
    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)

    monkeypatch.setattr(
        activity_gate,
        "wait_for_foreground",
        lambda: pytest.fail("Foreground review waited on its own lease"),
    )

    @contextmanager
    def source_read(**options):
        assert options == {"read_only": True, "repertoire_snapshot": False}
        assert (
            not activity_gate.in_background and activity_gate.foreground_requests_active
        )
        snapshot = {
            "id": 1,
            "card_id": "shadow-card",
            "revision": 3,
            "start_fen": manifest["decisions"][0]["fen"],
            "moves_json": '["e2e4","e7e5","g1f3","b8c6","f1b5"]',
            "trained_color": "white",
            "effective_trained_color": "white",
        }

        def source_row(statement, *_arguments):
            row = (
                (False,)
                if statement
                == "SELECT to_regclass('public.study_position_issued_plans') IS NOT NULL"
                else snapshot
            )
            return SimpleNamespace(fetchone=lambda: row)

        yield SimpleNamespace(execute_native=source_row)

    monkeypatch.setattr(postgres, "connection", source_read)
    submitted = []

    def dispatch(name, payload, **options):
        assert (
            not activity_gate.in_background and activity_gate.foreground_requests_active
        )
        submitted.append((name, payload, options))
        return {"persisted": True}

    monkeypatch.setattr(command_dispatch, "dispatch_command", dispatch)
    response = TestClient(main.app).post(
        "/api/cards/shadow-card/review" + ("/reconcile" if reconciling else ""),
        json={
            "outcome": "correct",
            "attempt_id": completion["attempt_id"],
            "queue_entry_id": 101,
            "opening_evidence_completion": completion,
        },
    )
    assert response.status_code == 200
    assert (
        submitted[0][0] == ("cards.review.reconcile" if reconciling else "cards.review")
        and submitted[0][1]["prepared_manifest"] == manifest
    )
    assert submitted[0][2].get("background", False) is False
    assert not activity_gate.foreground_requests_active




def test_opening_checkpoint_dispatches_as_background_without_changing_review_dispatch(
    monkeypatch,
):
    monkeypatch.setattr(postgres, "configured", lambda: True)
    from app import main, command_dispatch
    from app.contracts.transport.study import ReviewRequest
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/opening-evidence-manifest.json"
        ).read_text()
    )
    request = OpeningEvidenceCheckpoint(
        attempt_id="background-checkpoint",
        manifest=fixture,
        origin_queue_entry_id=101,
        started_at="2026-09-30T12:00:00Z",
        study_timezone="UTC",
    )
    dispatched = []

    monkeypatch.setattr(
        command_dispatch,
        "dispatch_command",
        lambda name, payload, **options: (
            dispatched.append((name, payload, options)) or {"persisted": True}
        ),
    )
    for _ in range(2):
        main.opening_checkpoint(
            request, idempotency_key="checkpoint-key"
        )
    main.review(
        "card",
        ReviewRequest(outcome="correct", queue_entry_id=101),
        idempotency_key="review-key",
    )
    assert dispatched[0] == dispatched[1]
    assert dispatched[0] == (
        "opening_evidence.checkpoint",
        {"checkpoint": request.model_dump(mode="json")},
        {"idempotency_key": "checkpoint-key", "background": True},
    )
    assert dispatched[2][0] == "cards.review"
    assert dispatched[2][2].get("background", False) is False


def test_opening_checkpoint_request_is_background_without_a_client_work_class_header(
    monkeypatch,
):
    monkeypatch.setattr(postgres, "configured", lambda: True)
    from fastapi.testclient import TestClient
    from app import main, command_dispatch

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/opening-evidence-manifest.json"
        ).read_text()
    )
    request = {
        "attempt_id": "background-http",
        "manifest": fixture,
        "origin_queue_entry_id": 101,
        "started_at": "2026-09-30T12:00:00Z",
        "study_timezone": "UTC",
    }
    observed_scopes = []

    @contextmanager
    def scope(work_class):
        observed_scopes.append(work_class)
        yield

    @asynccontextmanager
    async def foreground_scope():
        observed_scopes.append("foreground")
        yield

    monkeypatch.setattr(main.activity_gate, "async_foreground", foreground_scope)
    monkeypatch.setattr(
        main.activity_gate, "background_request", lambda: scope("background")
    )
    monkeypatch.setattr(
        command_dispatch,
        "dispatch_command",
        lambda *args, **kwargs: {"persisted": True},
    )
    client = TestClient(main.app)
    assert (
        client.post("/api/opening-evidence/checkpoints", json=request).status_code
        == 200
    )
    assert observed_scopes == ["background"]
    observed_scopes.clear()
    assert (
        client.post(
            "/api/cards/card/review", json={"outcome": "correct", "queue_entry_id": 101}
        ).status_code
        == 200
    )
    assert observed_scopes == ["foreground"]


def test_shadow_manifest_fixture_matches_backend_producer_and_prescribed_revision():
    from app.services.opening_decision_evidence import decision_manifest

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/opening-evidence-manifest.json"
        ).read_text()
    )
    snapshot = {
        "id": 1,
        "card_id": "shadow-card",
        "revision": 3,
        "start_fen": fixture["decisions"][0]["fen"],
        "moves_json": '["e2e4","e7e5","g1f3","b8c6","f1b5"]',
        "trained_color": "white",
    }
    assert decision_manifest(snapshot, "shadow-repertoire") == fixture
    changed = {**snapshot, "revision": 4}
    assert (
        decision_manifest(changed, "shadow-repertoire")["manifest_id"]
        != fixture["manifest_id"]
    )


def test_legacy_review_dispatch_preserves_exact_payload_and_command_fingerprint(
    monkeypatch,
):
    monkeypatch.setattr(postgres, "configured", lambda: True)
    from app import main, command_dispatch
    from app.contracts.transport.study import ReviewRequest
    from app.command_gateway import request_digest

    requests = []

    monkeypatch.setattr(
        command_dispatch,
        "dispatch_command",
        lambda name, payload, **options: (
            requests.append((name, payload)) or {"persisted": True}
        ),
    )
    request = ReviewRequest(
        outcome="correct", guided=False, queue_entry_id=101, attempt_id="legacy-id"
    )
    main.review("card", request, idempotency_key="legacy-key")
    # The historical contract must not inherit optional fields added to today's
    # request model. Pin the actual original payload and fingerprint independently.
    expected = {
        "card_id": "card",
        "review": {
            "outcome": "correct",
            "guided": False,
            "queue_entry_id": 101,
            "recorded_at": None,
            "expected_review_id": None,
            "expected_revision": None,
            "attempt_id": "legacy-id",
        },
    }
    assert requests == [("cards.review", expected)]
    digest = hashlib.sha256(
        json.dumps(
            ["cards.review", expected], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    assert digest == "5c4cc71a036f44a80f2a8fc0c9cf4d10315a3937508051487c826d9e010ae474"
    assert request_digest("cards.review", expected) == digest
    assert (
        request_digest(
            "cards.review", {**expected, "prepared_manifest": {"derived": True}}
        )
        == digest
    )


def test_shadow_chess_validation_closes_read_connection_before_traversal(monkeypatch):
    from tempo_storage.opening_evidence import storage

    monkeypatch.setattr(storage, "postgres_is_configured", lambda: True)
    from app.study import position_checkpoint as service
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/opening-evidence-manifest.json"
        ).read_text()
    )
    request = OpeningEvidenceCheckpoint(
        attempt_id="attempt",
        manifest=fixture,
        origin_queue_entry_id=101,
        started_at="2026-09-30T12:00:00Z",
        study_timezone="UTC",
    )
    active = False

    @contextmanager
    def read(**options):
        nonlocal active
        active = True

        def source_row(statement, *_arguments):
            row = (
                (False,)
                if statement
                == "SELECT to_regclass('public.study_position_issued_plans') IS NOT NULL"
                else {"trained_color": "white", "effective_trained_color": "white"}
            )
            return SimpleNamespace(fetchone=lambda: row)

        yield SimpleNamespace(execute_native=source_row)
        active = False

    def derive(*args):
        assert not active, "Chess traversal held its database read connection"
        return fixture

    monkeypatch.setattr(service.postgres, "configured", lambda: True)
    monkeypatch.setattr(service, "read_connection", read)
    monkeypatch.setattr(service, "decision_manifest", derive)
    assert service.prepare_checkpoint(request) == fixture


def test_evidence_migration_is_unique_additive_and_matches_readiness():
    from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION

    directory = (
        Path(__file__).resolve().parents[2]
        / "database/tempo_storage/ddl/migrations"
    )
    migrations = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    assert [int(path.name[:3]) for path in migrations] == list(
        range(1, POSTGRES_SCHEMA_VERSION + 1)
    )
    migration = next(
        path
        for path in migrations
        if path.name.endswith("_opening_decision_evidence.sql")
    )
    source = migration.read_text()
    assert "INSERT INTO tempo_schema_migrations(version) VALUES(30)" in source
    assert "UPDATE cards SET" not in source and "UPDATE daily_queue SET" not in source
    assert "REFERENCES cards" not in source and "REFERENCES repertoires" not in source


def test_queue_evidence_is_explicitly_negotiated_for_live_window_and_prepared_clients(
    monkeypatch,
):
    from app import main

    calls = []

    def payload(limit=None, *, include_opening_evidence=False):
        calls.append((limit, include_opening_evidence))
        return {
            "cards": [],
            "count": 0,
            **(
                {"opening_evidence_study_timezone": "UTC"}
                if include_opening_evidence
                else {}
            ),
        }

    monkeypatch.setattr(main, "_queue_payload", payload)
    for route in (main.queue_today, main.queue_window, main.prepared_queue):
        legacy = route()
        assert "opening_evidence_study_timezone" not in legacy
        enabled = route(include_opening_evidence=True)
        assert enabled["opening_evidence_study_timezone"] == "UTC"
    assert calls == [
        (None, False),
        (None, True),
        (20, False),
        (20, True),
        (None, False),
        (None, True),
    ]




def test_review_completion_requires_a_resolved_actual_queue_binding(
    monkeypatch, review_policy_settings
):
    import pytest
    from fastapi import HTTPException
    from app import review_commands
    from app.services import learning_review_adapter

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/opening-evidence-manifest.json"
        ).read_text()
    )
    monkeypatch.setattr(
        learning_review_adapter, "lock_queue_date_for_position", lambda *args: None
    )
    completion = {
        "attempt_id": "unbound-repeat",
        "manifest": fixture,
        "origin_queue_entry_id": 101,
        "queue_entry_id": None,
        "started_at": "2026-09-30T12:00:00Z",
        "study_timezone": "UTC",
        "terminal": {
            "state": "complete",
            "final_sequence": 0,
            "ended_at": "2026-09-30T12:01:00Z",
        },
    }

    def empty_query(statement, *_arguments):
        assert statement.lstrip().startswith("SELECT"), (
            "Unbound review attempted a data mutation"
        )
        return SimpleNamespace(fetchone=lambda: None)

    def native(statement, *_arguments):
        if (
            statement
            == "SELECT to_regclass('public.study_position_issued_plans') IS NOT NULL"
        ):
            return SimpleNamespace(fetchone=lambda: (False,))
        if statement == "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))":
            assert _arguments == (("tempo:card-edit:shadow-card",),)
        return empty_query(statement, *_arguments)

    database = SimpleNamespace(execute=empty_query, execute_native=native)
    with pytest.raises(HTTPException) as rejected:
        review_commands.submit_review(
            database,
            {
                "card_id": "shadow-card",
                "review": {
                    "outcome": "correct",
                    "attempt_id": "unbound-repeat",
                    "opening_evidence_completion": completion,
                },
            },
        )
    assert rejected.value.detail["code"] == "opening_evidence_conflict"
    assert "queue entry" in rejected.value.detail["message"]


def test_scoped_color_adapter_preserves_raw_legacy_presentation():
    from app.study.position_checkpoint import _manifest_snapshot

    snapshot = {"trained_color": None, "effective_trained_color": "black", "id": 17}
    assert _manifest_snapshot(snapshot) == {**snapshot, "trained_color": "black"}
    assert snapshot["trained_color"] is None


def test_scoped_color_adapter_rejects_unknown_and_explicit_mismatch():
    import pytest
    from app.study.position_checkpoint import _manifest_snapshot

    for raw_color, effective_color in [
        (None, None),
        (None, "unknown"),
        ("white", "black"),
        ("unknown", "white"),
    ]:
        with pytest.raises(ValueError):
            _manifest_snapshot(
                {"trained_color": raw_color, "effective_trained_color": effective_color}
            )


def test_evidence_migration_captures_valid_scope_color_without_overwriting_context():
    source = (
        Path(__file__).resolve().parents[2]
        / "database/tempo_storage/ddl/migrations/030_opening_decision_evidence.sql"
    ).read_text()
    assert (
        "effective_trained_color TEXT NOT NULL CHECK(effective_trained_color IN ('white','black'))"
        in source
    )
    assert (
        "WHERE line.repertoire_id=scope.repertoire_id ORDER BY line.created_at,line.id LIMIT 1"
        in source
    )
    binding = source.split("CREATE FUNCTION opening_evidence_bind_queue", 1)[1].split(
        "CREATE FUNCTION opening_evidence_capture_queue_context", 1
    )[0]
    assert "COALESCE(card.trained_color," in binding
    assert "learner.effective_trained_color IN ('white','black')" in binding
    assert "ON CONFLICT DO NOTHING" in binding
    assert "DO UPDATE" not in binding


@pytest.mark.parametrize(
    ("has_receipt", "idempotent", "requeue_id", "expected_corrections"),
    [
        (False, False, 202, 1),
        (
            True,
            False,
            202,
            0,
        ),  # Receipt replay retains the original idempotent=False result.
        (False, True, 202, 0),
        (False, False, None, 0),
        (False, None, 202, 0),
    ],
)
@pytest.mark.parametrize("reconciling", [False, True])
def test_review_requeue_context_correction_only_runs_for_fresh_review(
    monkeypatch,
    review_policy_settings,
    has_receipt,
    idempotent,
    requeue_id,
    expected_corrections,
    reconciling,
):
    from app import review_commands
    from app.study import position_checkpoint as checkpoint_owner
    from app.services import learning_review_adapter
    from tempo_learning_review import (
        apply_review as installed_apply_review,
        ReviewResult,
    )

    manifest = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/opening-evidence-manifest.json"
        ).read_text()
    )
    completion = {
        "attempt_id": "context-authority",
        "manifest": manifest,
        "origin_queue_entry_id": 101,
        "queue_entry_id": 101,
        "started_at": "2026-09-30T12:00:00Z",
        "study_timezone": "UTC",
        "terminal": {
            "state": "complete",
            "final_sequence": 0,
            "ended_at": "2026-09-30T12:01:00Z",
        },
    }
    corrections = []
    validation_order = []
    result = {"persisted": True, "requeue_entry_id": requeue_id}
    if idempotent is not None:
        result["idempotent"] = idempotent

    def execute(statement, parameters):
        if "FROM review_attempt_receipts" in statement:
            row = (
                {
                    "card_id": manifest["card_id"],
                    "queue_entry_id": 101,
                    "outcome": "correct",
                    "guided": False,
                    "completed_at": None,
                    "result_json": json.dumps(result),
                    "request_json": None,
                }
                if has_receipt
                else None
            )
        elif "SELECT revision" in statement:
            row = (manifest["card_revision"],)
        elif "SELECT status" in statement:
            row = ("queued",)
        else:
            row = None
        return SimpleNamespace(fetchone=lambda: row)

    def execute_native(statement, parameters):
        if (
            statement
            == "SELECT to_regclass('public.study_position_issued_plans') IS NOT NULL"
        ):
            return SimpleNamespace(fetchone=lambda: (False,))
        if "INSERT INTO opening_evidence_queue_contexts" in statement:
            assert validation_order == ["validated", "reviewed", "completed"]
            corrections.append(parameters)
        return SimpleNamespace(
            fetchone=lambda: (
                None if "FROM deleted_cards" in statement else ("complete",)
            )
        )

    database = SimpleNamespace(
        execute=execute,
        execute_native=execute_native,
        raw=SimpleNamespace(execute=lambda *args: None),
    )

    def apply_review(adapter, command, context, *, position=False):
        assert position is False
        assert adapter.database is database and validation_order == ["validated"]
        validation_order.append("reviewed")
        return ReviewResult.from_dict(result)

    monkeypatch.setattr(
        learning_review_adapter, "lock_queue_date_for_position", lambda *arguments: None
    )
    monkeypatch.setattr(
        f"{installed_apply_review.__module__}.apply_review", apply_review
    )

    def persist_completion(connection, payload, **options):
        assert connection is database and options == {"completing_review": True}
        validation_order.append("validated")

    def complete_evidence(connection, *arguments):
        assert connection is database
        validation_order.append("completed")

    monkeypatch.setattr(
        checkpoint_owner, "persist_checkpoint", persist_completion
    )
    monkeypatch.setattr(
        checkpoint_owner, "complete_review_evidence", complete_evidence
    )
    handler = (
        review_commands.reconcile_review
        if reconciling
        else review_commands.submit_review
    )
    assert (
        handler(
            database,
            {
                "card_id": manifest["card_id"],
                "review": {
                    "outcome": "correct",
                    "attempt_id": completion["attempt_id"],
                    "queue_entry_id": 101,
                    "opening_evidence_completion": completion,
                },
                "prepared_manifest": manifest,
            },
        )
        == result
    )
    assert len(corrections) == expected_corrections
    if corrections:
        assert corrections == [
            (
                requeue_id,
                manifest["presentation_snapshot_id"],
                manifest["repertoire_id"],
                manifest["trained_color"],
            )
        ]


@pytest.mark.parametrize("background", [True, False])
def test_opening_checkpoint_receipt_read_uses_background_admission(
    monkeypatch, background
):
    monkeypatch.setattr(postgres, "configured", lambda: True)
    from fastapi.testclient import TestClient
    from app import main

    observed_scopes = []
    reads = []

    @contextmanager
    def scope(work_class):
        observed_scopes.append(work_class)
        yield

    @asynccontextmanager
    async def foreground_scope():
        observed_scopes.append("foreground")
        yield

    monkeypatch.setattr(main.activity_gate, "async_foreground", foreground_scope)
    monkeypatch.setattr(
        main.activity_gate, "background_request", lambda: scope("background")
    )

    def read(operation_id, **options):
        reads.append((operation_id, options))
        assert observed_scopes == ["background" if background else "foreground"]
        return {"state": "complete", "response": {"persisted": True}}

    monkeypatch.setattr(main, "read_operation", read)
    headers = {"X-Tempo-Work-Class": "background"} if background else {}
    response = TestClient(main.app).get(
        "/api/operations/opening-checkpoint:receipt", headers=headers
    )
    assert response.status_code == 200
    assert response.json() == {"state": "complete", "response": {"persisted": True}}
    assert reads == [("opening-checkpoint:receipt", {"background": background})]


def test_reconciliation_conflict_rolls_back_new_evidence_and_preserves_saved_checkpoint(
    monkeypatch, review_policy_settings
):
    from app import review_commands
    from app.contracts.review_conflicts import ReviewConflict
    from app.study import position_checkpoint as evidence
    from app.services import learning_review_adapter
    from tempo_learning_review import apply_review as installed_apply_review

    manifest = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/opening-evidence-manifest.json"
        ).read_text()
    )
    completion = {
        "attempt_id": "atomic-reconcile",
        "manifest": manifest,
        "origin_queue_entry_id": 101,
        "queue_entry_id": 101,
        "started_at": "2026-09-30T12:00:00Z",
        "study_timezone": "UTC",
        "terminal": {
            "state": "complete",
            "final_sequence": 0,
            "ended_at": "2026-09-30T12:01:00Z",
        },
    }
    writes = ["saved-checkpoint"]
    checkpoints = []
    operations = []

    def transaction(statement, *arguments):
        operations.append(statement)
        if statement.startswith("SAVEPOINT"):
            checkpoints[:] = writes
        elif statement.startswith("ROLLBACK TO"):
            writes[:] = checkpoints

    def execute(statement, parameters):
        row = (
            (manifest["card_revision"],)
            if "SELECT revision" in statement
            else ("queued",)
            if "SELECT status" in statement
            else None
        )
        return SimpleNamespace(fetchone=lambda: row)

    database = SimpleNamespace(
        execute=execute,
        execute_native=execute,
        raw=SimpleNamespace(execute=transaction),
    )
    monkeypatch.setattr(
        learning_review_adapter, "lock_queue_date_for_position", lambda *args: None
    )

    def persist(*args, **options):
        writes.append("new-completion-events")

    def conflict(*args, **options):
        assert writes == ["saved-checkpoint", "new-completion-events"], (
            "Reconciliation bypassed evidence validation/persistence"
        )
        raise ReviewConflict("content_changed", "Saved content changed")

    monkeypatch.setattr(evidence, "persist_checkpoint", persist)
    monkeypatch.setattr(
        evidence,
        "complete_review_evidence",
        lambda *args: pytest.fail("Conflicted review completed evidence"),
    )
    monkeypatch.setattr(f"{installed_apply_review.__module__}.apply_review", conflict)
    result = review_commands.reconcile_review(
        database,
        {
            "card_id": manifest["card_id"],
            "review": {
                "outcome": "correct",
                "attempt_id": completion["attempt_id"],
                "queue_entry_id": 101,
                "opening_evidence_completion": completion,
            },
            "prepared_manifest": manifest,
        },
    )
    assert result == {
        "persisted": False,
        "conflict": {
            "code": "content_changed",
            "message": "Saved content changed",
            "retryable": False,
        },
    }
    assert writes == ["saved-checkpoint"]
    assert any(
        statement.startswith("ROLLBACK TO SAVEPOINT") for statement in operations
    )


def test_reconciliation_digest_ignores_only_derived_preparation():
    from app.command_gateway import request_digest

    payload = {
        "card_id": "shadow-card",
        "review": {"attempt_id": "same-result", "outcome": "correct"},
    }
    digest = request_digest("cards.review.reconcile", payload)
    assert (
        request_digest(
            "cards.review.reconcile",
            {**payload, "prepared_manifest": {"derived": True}},
        )
        == digest
    )
    assert (
        request_digest(
            "cards.review.reconcile",
            {**payload, "review": {**payload["review"], "outcome": "again"}},
        )
        != digest
    )


@pytest.fixture
def attempt_status_boundary(monkeypatch):
    """The recovery read returns status only through the real reader-role boundary."""
    from fastapi.testclient import TestClient
    from app import main
    from app.services.activity_gate import activity_gate
    from threading import Event

    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    monkeypatch.delenv("TEMPO_FOREGROUND_ACTIVITY_URL", raising=False)
    boundary = SimpleNamespace(
        attempt={"attempt_id": "recovery-attempt", "state": "complete"},
        sections=[], sql=[], active_connections=0, reached=Event(),
    )
    original_wait = activity_gate.wait_for_foreground

    def wait():
        assert activity_gate.in_background
        boundary.reached.set()
        original_wait()

    monkeypatch.setattr(activity_gate, "wait_for_foreground", wait)

    def execute(statement, parameters):
        assert " ".join(statement.split()) == (
            "SELECT attempt_id,state FROM opening_evidence_attempts WHERE attempt_id=%s"
        )
        assert parameters == ("recovery-attempt",)
        boundary.sql.append((statement, parameters))
        return SimpleNamespace(fetchone=lambda: boundary.attempt)

    @contextmanager
    def connection(**options):
        boundary.sections.append(options)
        boundary.active_connections += 1
        try:
            yield SimpleNamespace(execute=execute, execute_native=execute)
        finally:
            boundary.active_connections -= 1

    monkeypatch.setattr(postgres, "connection", connection)
    boundary.client = TestClient(main.app)
    return boundary


@pytest.mark.parametrize("state", ["complete", "partial", None])
def test_recovery_attempt_status_background_read_preserves_state_and_reader_role(
    attempt_status_boundary, state
):
    from app.services.activity_gate import activity_gate
    boundary = attempt_status_boundary
    boundary.attempt = None if state is None else {"attempt_id": "recovery-attempt", "state": state}
    response = boundary.client.get(
        "/api/opening-evidence/attempts/recovery-attempt",
        headers={"X-Tempo-Work-Class": "background"},
    )
    assert response.status_code == (404 if state is None else 200)
    if state is not None:
        assert response.json() == boundary.attempt
    assert boundary.sections == [{
        "read_only": True, "background": True, "authoritative": False,
        "pool_timeout_seconds": None,
    }]
    assert len(boundary.sql) == 1
    assert boundary.active_connections == activity_gate.active_background_sections == 0


def test_recovery_attempt_status_waits_for_foreground_before_opening_reader(attempt_status_boundary):
    from concurrent.futures import ThreadPoolExecutor
    from app.services.activity_gate import activity_gate
    boundary = attempt_status_boundary
    with ThreadPoolExecutor(max_workers=1) as requests:
        with activity_gate.foreground():
            pending = requests.submit(
                boundary.client.get, "/api/opening-evidence/attempts/recovery-attempt",
                headers={"X-Tempo-Work-Class": "background"},
            )
            assert boundary.reached.wait(5)
            response = pending.result(timeout=5)
            assert response.status_code == 503
            assert response.headers["Retry-After"] == "1"
            assert not boundary.sections and not boundary.sql
        retry = requests.submit(
            boundary.client.get, "/api/opening-evidence/attempts/recovery-attempt",
            headers={"X-Tempo-Work-Class": "background"},
        ).result(timeout=5)
    assert retry.status_code == 200 and retry.json() == boundary.attempt
    assert len(boundary.sections) == 1


def test_recovery_attempt_status_foreground_read_does_not_wait_on_itself(attempt_status_boundary, monkeypatch):
    from app.services.activity_gate import activity_gate
    monkeypatch.setattr(activity_gate, "wait_for_foreground", lambda: pytest.fail("Foreground read waited on itself"))
    boundary = attempt_status_boundary
    response = boundary.client.get("/api/opening-evidence/attempts/recovery-attempt")
    assert response.status_code == 200 and response.json() == boundary.attempt
    assert boundary.sections == [{"read_only": True, "repertoire_snapshot": False}]
    assert boundary.active_connections == 0
    assert not activity_gate.foreground_requests_active
