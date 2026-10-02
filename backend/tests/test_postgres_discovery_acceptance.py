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

    def execute(self, statement, parameters=()):
        return self.execute_native(statement.replace("?", "%s"), parameters)

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
    assert any("INSERT INTO discovery_admission_intents" in statement for statement, _ in database.statements)
    assert any("canonical_prefix_revision" in statement for statement, _ in database.statements)


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


def test_postgres_discovery_terminal_readmission_requeues_existing_intent_once(monkeypatch):
    queued = []
    monkeypatch.setattr(discovery_commands,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda database, kind, key, payload, *, priority:
                        queued.append((kind, key, payload)))
    database = Database(prior={"id": "intent-one", "evidence_fingerprint": "revision-one",
                              "state": "preparing"}, task={"state": "failed"})
    result = discovery_commands.accept_discovery(database, prepared())
    assert result == {"status": "preparing", "intent_id": "intent-one"}
    assert queued == [("discovery_admission", "intent-one", {"intent_id": "intent-one"})]
    assert all("INSERT INTO discovery_admission_intents" not in statement
               for statement, _parameters in database.statements)


def test_postgres_discovery_acceptance_rejects_stale_evidence_before_write():
    database = Database(opportunity={"id": "opening-one", "repertoire_id": "white-openings",
                                    "evidence_fingerprint": "revision-two", "card_id": None,
                                    "status": "active"})
    with pytest.raises(HTTPException) as error:
        discovery_commands.accept_discovery(database, prepared())
    assert error.value.status_code == 409
    assert all(not statement.startswith("INSERT") for statement, _ in database.statements)


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


@pytest.mark.parametrize("opportunity_revision,expected_status", [(1, None), (0, 404)])
def test_canonical_prefix_discovery_acceptance_requires_current_scope_revision(monkeypatch, opportunity_revision, expected_status):
    from app.services import canonical_prefix
    monkeypatch.setattr(canonical_prefix, "read_prefix", lambda *_args, **_kwargs: {"revision": 1})
    monkeypatch.setattr(discovery_commands, "enqueue_compact_postgres_task_in_transaction", lambda *_args, **_kwargs: None)
    database = Database(opportunity={"id": "opening-one", "repertoire_id": "white-openings",
        "canonical_prefix_revision": opportunity_revision, "evidence_fingerprint": "revision-one", "card_id": None, "status": "active"})
    if expected_status:
        with pytest.raises(HTTPException) as error:
            discovery_commands.accept_discovery(database, prepared())
        assert error.value.status_code == expected_status
        assert all(not statement.startswith("INSERT") for statement, _ in database.statements)
    else:
        assert discovery_commands.accept_discovery(database, prepared())["status"] == "preparing"
    assert "canonical_prefix_revision" in database.statements[1][0]


def test_postgres_resurfaced_acceptance_keeps_revision_identity_and_same_choice_retries(monkeypatch):
    queued = []
    monkeypatch.setattr(discovery_commands, "enqueue_compact_postgres_task_in_transaction",
                        lambda _db, _kind, key, _payload, **_kwargs: queued.append(key))
    class RevisionDatabase(Database):
        def __init__(self):
            super().__init__(opportunity={"id": "opening-one", "repertoire_id": "white-openings",
                "evidence_fingerprint": "revision-two", "card_id": None, "status": "active"})
            self.intents = {"revision-one": {"id": "legacy-intent", "evidence_fingerprint": "revision-one", "state": "queued"}}
        def execute_native(self, statement, parameters=()):
            if statement.startswith("SELECT id,evidence_fingerprint,state"):
                self.statements.append((statement, parameters))
                return Cursor(self.intents.get(parameters[2] if len(parameters) == 3 else "revision-one"))
            if statement.startswith("INSERT INTO discovery_admission_intents"):
                self.intents[parameters[3]] = {"id": parameters[0], "evidence_fingerprint": parameters[3], "state": "queued"}
            return super().execute_native(statement, parameters)
    db = RevisionDatabase()
    payload = {**prepared(), "evidence_fingerprint": "revision-two"}
    accepted = discovery_commands.accept_discovery(db, payload)
    assert accepted["intent_id"] != "legacy-intent"
    assert discovery_commands.accept_discovery(db, payload) == accepted
    assert queued == [accepted["intent_id"]]
    assert db.intents["revision-one"]["id"] == "legacy-intent"
