"""The capture and Study admission share the exact caller-owned writer."""

from datetime import datetime, timezone
from app.study.policy.clock import study_calendar_at

CALENDAR = study_calendar_at(datetime(2026, 11, 12, 2, tzinfo=timezone.utc), "America/New_York")

import json
import uuid
import chess
import pytest
from fastapi import HTTPException
from app.contracts.transport.tactics import TacticCaptureRequest
from app.product_composition import tactic_capture as composition
from tempo_storage.tactics import captures as receipt_storage
from tempo_storage.study import captured_tactic as study_storage


def test_tactic_capture_borrowed_admission_preserves_lock_order_and_writer(monkeypatch):
    database = object()
    calls = []

    def record(name, result=None):
        def run(db, *args):
            assert db is database
            calls.append(name)
            return result

        return run

    monkeypatch.setattr(composition, "lock_daily_tactic_admission", record("daily"))
    monkeypatch.setattr(receipt_storage, "lock_capture", record("capture-lock"))
    monkeypatch.setattr(receipt_storage, "capture_receipt", record("receipt-read"))
    for name in (
        "ensure_capture_repertoire",
        "link_capture_card",
        "reopen_capture_card",
        "lock_capture_queue",
        "mark_capture_guided",
    ):
        monkeypatch.setattr(study_storage, name, record(name))
    monkeypatch.setattr(study_storage, "create_capture_card", record("create", True))
    monkeypatch.setattr(
        study_storage,
        "capture_card",
        record(
            "read-card",
            {
                "content_type": "tactic",
                "archived": 0,
                "superseded_by": None,
                "pending_validation": 0,
                "start_fen": chess.STARTING_FEN,
                "moves_json": json.dumps(["e2e4"]),
            },
        ),
    )

    def place(db, card, after, state, reason, *, queue_date):
        assert queue_date == "2026-11-11"
        assert db is database and (after, state, reason) == (
            4,
            "guided",
            "Captured tactic",
        )
        calls.append("place")

    def save(db, *args):
        assert db is database
        assert list(json.loads(args[-1])) == [
            "capture_id",
            "card_id",
            "reused",
            "queued",
            "introduced",
        ]
        calls.append("save-receipt")

    monkeypatch.setattr(composition, "ensure_card_queued_after", place)
    monkeypatch.setattr(receipt_storage, "save_capture_receipt", save)
    request = TacticCaptureRequest(
        capture_id=uuid.uuid4(), starting_fen=chess.STARTING_FEN, moves=["e2e4"]
    )
    result = composition.capture_tactic(database, request, calendar=CALENDAR)
    assert list(result) == ["capture_id", "card_id", "reused", "queued", "introduced"]
    assert calls == [
        "daily",
        "capture-lock",
        "receipt-read",
        "ensure_capture_repertoire",
        "create",
        "read-card",
        "link_capture_card",
        "reopen_capture_card",
        "lock_capture_queue",
        "place",
        "mark_capture_guided",
        "save-receipt",
    ]


def test_tactic_capture_translates_owned_validation_and_replay_conflicts(monkeypatch):
    database = object()
    monkeypatch.setattr(composition, "lock_daily_tactic_admission", lambda *_: None)
    monkeypatch.setattr(receipt_storage, "lock_capture", lambda *_: None)
    monkeypatch.setattr(
        receipt_storage,
        "capture_receipt",
        lambda *_: {"request_json": "different", "result_json": "{}"},
    )
    request = TacticCaptureRequest(
        capture_id=uuid.uuid4(), starting_fen=chess.STARTING_FEN, moves=["e2e4"]
    )
    with pytest.raises(HTTPException) as conflict:
        composition.capture_tactic(database, request, calendar=CALENDAR)
    assert conflict.value.status_code == 409
    with pytest.raises(HTTPException) as invalid:
        composition.capture_tactic(
            database, request.model_copy(update={"moves": ["e2e5"]}), calendar=CALENDAR
        )
    assert invalid.value.status_code == 422


def test_captured_tactic_public_protocol_import_does_not_start_implementations():
    import os
    import subprocess
    import sys

    script = """
import importlib.abc, sys
class Forbid(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('app.product_composition', 'app.services', 'tempo_storage', 'psycopg', 'redis', 'celery')):
            raise AssertionError('public contract imported implementation: ' + fullname)
sys.meta_path.insert(0, Forbid())
from app.study.interfaces.captured_tactic import CapturedTacticAdmission
from app.contracts.tactic_capture import ValidatedCapture
assert CapturedTacticAdmission and ValidatedCapture
"""
    subprocess.run(
        [sys.executable, "-B", "-c", script], check=True, env=os.environ.copy()
    )


def test_disposable_capture_proof_binds_database_before_owner_work(monkeypatch):
    """The direct proof entry point composes the same authorities as the app."""
    import os
    import runpy
    import sys
    from pathlib import Path
    from unittest.mock import patch
    from app.product_composition import database_infrastructure
    from tempo_storage import postgres

    calls = []
    original_path = sys.path[:]
    original_environment = dict(os.environ)
    monkeypatch.setattr(
        database_infrastructure,
        "bind_database_infrastructure",
        lambda: calls.append("bind"),
    )
    try:
        with patch.dict(os.environ):
            proof = runpy.run_path(
                str(
                    Path(__file__).resolve().parents[2]
                    / "database/tests/check_postgres_tactic_capture.py"
                )
            )
            assert calls == []  # Importing the proof remains inert.
            os.environ.pop("TEMPO_TEST_INSTANCE", None)
            with pytest.raises(RuntimeError, match="disposable PostgreSQL instance"):
                proof["main"]()
            assert calls == []
            os.environ["TEMPO_TEST_INSTANCE"] = "disposable"

            def stop_before_connection():
                calls.append("connection")
                raise RuntimeError("stopped before database access")

            monkeypatch.setattr(postgres, "connection", stop_before_connection)
            with pytest.raises(RuntimeError, match="stopped before database access"):
                proof["main"]()
            assert calls == ["bind", "connection"]
    finally:
        sys.path[:] = original_path
    assert dict(os.environ) == original_environment
    assert sys.path == original_path
