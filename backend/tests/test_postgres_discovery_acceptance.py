"""Discovery acceptance validates a prepared choice before durable admission."""

from fastapi import HTTPException
import pytest

from app import command_dispatch, discovery_commands, main, postgres_store
from app.command_gateway import request_digest
from app.models import DiscoveryAcceptanceRequest


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Database:
    def __init__(self, *, prior=None, opportunity=None, task=None):
        self.prior = prior
        self.opportunity = opportunity
        self.task = task
        self.statements = []

    def execute_native(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        if statement.startswith("SELECT id,evidence_fingerprint,state"):
            return Cursor(self.prior)
        if statement.startswith("SELECT id,repertoire_id,evidence_fingerprint"):
            return Cursor(self.opportunity)
        if statement.startswith("SELECT state FROM background_tasks"):
            return Cursor(self.task)
        return Cursor()


def prepared():
    return {"opportunity_id": "opening-one", "selected_move_uci": "e2e4",
            "evidence_fingerprint": "revision-one", "repertoire_id": "white-openings",
            "starting_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "preview_moves_uci": ["e2e4"],
            "recommendation": {"move_uci": "e2e4", "preview_moves_uci": ["e2e4"]}}


def test_postgres_discovery_acceptance_creates_one_intent_and_durable_task(monkeypatch):
    queued = []
    monkeypatch.setattr(discovery_commands,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda database, kind, key, payload, *, priority:
                        queued.append((kind, key, payload, priority)))
    database = Database(opportunity={"id": "opening-one", "repertoire_id": "white-openings",
                                    "evidence_fingerprint": "revision-one", "card_id": None,
                                    "status": "active"})
    response = discovery_commands.accept_discovery(database, prepared())
    assert response["status"] == "preparing"
    assert len(queued) == 1
    assert queued[0][:3] == ("discovery_admission", response["intent_id"],
                             {"intent_id": response["intent_id"]})
    assert "FOR UPDATE" in database.statements[1][0]
    assert "INSERT INTO discovery_admission_intents" in database.statements[3][0]


def test_postgres_discovery_acceptance_replays_prior_without_duplicate_task(monkeypatch):
    queued = []
    monkeypatch.setattr(discovery_commands,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda *_args, **_kwargs: queued.append(True))
    database = Database(prior={"id": "intent-one", "evidence_fingerprint": "revision-one",
                              "state": "preparing"}, task={"state": "leased"})
    assert discovery_commands.accept_discovery(database, prepared()) == {
        "status": "preparing", "intent_id": "intent-one",
    }
    assert queued == []
    assert all("INSERT INTO discovery_admission_intents" not in statement
               for statement, _parameters in database.statements)


def test_postgres_discovery_acceptance_rejects_stale_evidence_before_write():
    database = Database(opportunity={"id": "opening-one", "repertoire_id": "white-openings",
                                    "evidence_fingerprint": "revision-two", "card_id": None,
                                    "status": "active"})
    with pytest.raises(HTTPException) as error:
        discovery_commands.accept_discovery(database, prepared())
    assert error.value.status_code == 409
    assert len(database.statements) == 2


def test_postgres_discovery_acceptance_route_keeps_idempotency_key(monkeypatch):
    calls = []
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(discovery_commands, "prepare_discovery_acceptance",
                        lambda *_args: prepared())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key, background=False:
                        calls.append((name, payload, idempotency_key, background))
                        or {"status": "preparing", "intent_id": "intent-one"})
    request = DiscoveryAcceptanceRequest(selected_move_uci="e2e4",
                                         evidence_fingerprint="revision-one")
    assert main.accept_discovery_continuation("opening-one", request, "accept-one") == {
        "status": "preparing", "intent_id": "intent-one",
    }
    assert calls == [("discovery.accept", prepared(), "accept-one", False)]
    changed_preparation = {**prepared(), "recommendation": {"move_uci": "e2e4",
                                                       "preview_moves_uci": ["e2e4", "e7e5"]}}
    assert request_digest("discovery.accept", prepared()) == request_digest(
        "discovery.accept", changed_preparation,
    )
