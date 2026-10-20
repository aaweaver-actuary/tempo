"""Discovery acceptance policy uses domain facts, never SQL-emulating fakes."""

from dataclasses import replace
import hashlib

import pytest

from app.services.discovery_acceptance_application import DiscoveryAcceptanceError, accept_discovery
from app.services.discovery_acceptance_contracts import (
    DiscoveryChoice, DiscoveryOpportunity, DiscoveryScope, LockedDiscoveryOpportunity,
    MatchingDiscoveryIntent,
)
from tempo_chess_core import card_id


class AcceptanceStore:
    """Stateful policy fake keyed by accepted choice and evidence revision."""

    def __init__(self, *, opportunity=None, current_scope=None, restricted=False):
        self.opportunity = opportunity
        self.current_scope = current_scope or DiscoveryScope()
        self.restricted = restricted
        self.intents = {}
        self.tasks = {}
        self.persisted = []
        self.enqueued = []
        self.calls = []
        self.concurrent_intent = None

    def has_matching_intent(self, choice):
        return choice in self.intents

    def lock_matching_intent(self, choice):
        self.calls.append(("matching_intent", choice))
        return self.intents.get(choice)

    def lock_opportunity_scope(self, opportunity_id):
        self.calls.append(("opportunity_scope", opportunity_id))
        if self.concurrent_intent:
            choice, intent = self.concurrent_intent
            self.intents[choice] = intent
        if self.opportunity is None:
            return None
        return LockedDiscoveryOpportunity(self.opportunity, self.current_scope, self.restricted)

    def task_state(self, intent_id):
        self.calls.append(("task_state", intent_id))
        return self.tasks.get(intent_id)

    def requeue_intent(self, intent_id):
        self.enqueued.append(intent_id)
        self.tasks[intent_id] = "queued"

    def persist_intent_and_task(self, intent):
        assert intent.choice not in self.intents
        self.persisted.append(intent)
        self.intents[intent.choice] = MatchingDiscoveryIntent(intent.id, "preparing")
        self.requeue_intent(intent.id)


def prepared():
    return {"opportunity_id": "opening-one", "selected_move_uci": "e2e4",
            "evidence_fingerprint": "revision-one", "repertoire_id": "white-openings",
            "starting_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "preview_moves_uci": ["e2e4"],
            "recommendation": {"move_uci": "e2e4", "preview_moves_uci": ["e2e4"]}}


def choice(payload=None):
    payload = payload or prepared()
    return DiscoveryChoice(payload["opportunity_id"], payload["selected_move_uci"],
                           payload["evidence_fingerprint"])


def active_store(**changes):
    opportunity = DiscoveryOpportunity("white-openings", "active", None, "revision-one", DiscoveryScope())
    return AcceptanceStore(opportunity=replace(opportunity, **changes))


def accept(store, payload=None):
    payload = prepared() if payload is None else payload
    identity = choice(payload)
    intent_id = hashlib.sha256(
        f"{identity.opportunity_id}\0{identity.selected_move_uci}\0{identity.evidence_fingerprint}".encode()
    ).hexdigest()
    return accept_discovery(store, payload, intent_id=intent_id)


def test_postgres_discovery_acceptance_creates_one_intent_and_durable_task():
    store = active_store()
    result = accept(store)
    assert result["status"] == "preparing"
    assert store.enqueued == [result["intent_id"]]
    intent, = store.persisted
    assert intent.id == result["intent_id"] and intent.choice == choice()
    assert intent.repertoire_id == prepared()["repertoire_id"]
    assert intent.preview_moves_uci == ("e2e4",)
    assert intent.recommendation == prepared()["recommendation"]
    assert intent.starting_fen == prepared()["starting_fen"]
    assert intent.line_id == hashlib.sha256(
        f"white-openings\0{card_id(intent.starting_fen, ['e2e4'])}".encode()
    ).hexdigest()
    assert store.calls == [("matching_intent", choice()), ("opportunity_scope", "opening-one"),
                           ("matching_intent", choice())]
    assert accept(store) == result
    assert len(store.persisted) == len(store.enqueued) == 1


@pytest.mark.parametrize("intent_state,task_state,requeue", [
    ("preparing", "leased", False), ("preparing", "queued", False),
    ("preparing", "complete", False), ("preparing", "failed", True),
    ("preparing", None, True), ("failed", "leased", True),
    ("failed", "complete", True), ("queued", "failed", False),
    ("queued", None, False),
])
def test_postgres_discovery_terminal_readmission_policy(intent_state, task_state, requeue):
    store = AcceptanceStore()
    store.intents[choice()] = MatchingDiscoveryIntent("intent-one", intent_state)
    if task_state is not None:
        store.tasks["intent-one"] = task_state
    assert accept(store, {key: prepared()[key] for key in (
        "opportunity_id", "selected_move_uci", "evidence_fingerprint")}) == {
        "status": "preparing", "intent_id": "intent-one"}
    assert store.enqueued == (["intent-one"] if requeue else [])
    assert store.persisted == []
    assert not any(call[0] == "opportunity_scope" for call in store.calls)
    if intent_state == "queued":
        assert store.calls == [("matching_intent", choice())]
    if intent_state == "preparing" and requeue:
        accept(store)
        assert store.enqueued == ["intent-one"]


def test_postgres_discovery_acceptance_rechecks_duplicate_after_parent_lock():
    store = active_store()
    store.concurrent_intent = (choice(), MatchingDiscoveryIntent("concurrent-intent", "failed"))
    assert accept(store) == {"status": "preparing", "intent_id": "concurrent-intent"}
    # Preserve the post-lock duplicate return: only the initial replay path requeues.
    assert store.enqueued == store.persisted == []
    assert store.calls[-1] == ("matching_intent", choice())


@pytest.mark.parametrize("changes,status,detail", [
    ({"status": "dismissed"}, 404, "Active discovery not found"),
    ({"card_id": "saved-card"}, 409, "This discovery already has a saved decision card"),
    ({"evidence_fingerprint": "revision-two"}, 409, "Discovery evidence changed; refresh the preview"),
    ({"repertoire_id": "other"}, 409, "Discovery evidence changed; refresh the preview"),
])
def test_postgres_discovery_acceptance_rejects_changed_opportunity_before_write(changes, status, detail):
    store = active_store(**changes)
    with pytest.raises(DiscoveryAcceptanceError) as error:
        accept(store)
    assert (error.value.status_code, error.value.detail) == (status, detail)
    assert store.persisted == store.enqueued == []


def test_discovery_acceptance_rejects_missing_opportunity():
    with pytest.raises(DiscoveryAcceptanceError, match="Active discovery not found") as error:
        accept(AcceptanceStore())
    assert error.value.status_code == 404


@pytest.mark.parametrize("field,current,observed,restricted,accepted", [
    ("canonical_prefix_revision", 1, 1, False, True),
    ("canonical_prefix_revision", 1, 0, False, False),
    ("game_scope_generation", 2, 1, False, False),
    ("canonical_scope_source_revision", 3, 2, True, False),
    ("canonical_scope_preview_id", "new-preview", "old-preview", True, False),
    ("canonical_scope_preview_id", None, None, True, False),
    ("canonical_scope_source_revision", 3, 2, False, True),
    ("canonical_scope_preview_id", "new-preview", "old-preview", False, True),
    ("canonical_scope_preview_id", "preview", "preview", True, True),
])
def test_canonical_prefix_discovery_acceptance_requires_current_scope_revision(
        field, current, observed, restricted, accepted):
    baseline = DiscoveryScope(canonical_scope_preview_id="preview")
    store = active_store(scope=replace(baseline, **{field: observed}))
    store.current_scope = replace(baseline, **{field: current})
    store.restricted = restricted
    if accepted:
        assert accept(store)["status"] == "preparing"
    else:
        with pytest.raises(DiscoveryAcceptanceError) as error:
            accept(store)
        assert error.value.status_code == 404
        assert store.persisted == store.enqueued == []


@pytest.mark.parametrize("changes", [
    {"preview_moves_uci": []}, {"preview_moves_uci": ["d2d4"]},
    {"recommendation": {"move_uci": "d2d4"}},
])
def test_discovery_acceptance_rejects_unvalidated_choice(changes):
    store = active_store()
    with pytest.raises(DiscoveryAcceptanceError) as error:
        accept(store, {**prepared(), **changes})
    assert error.value.status_code == 409
    assert error.value.detail == "The chosen move is not a validated recommendation"
    assert store.persisted == store.enqueued == []


def test_postgres_resurfaced_acceptance_keeps_revision_identity_and_same_choice_retries():
    store = active_store(evidence_fingerprint="revision-two")
    store.intents[choice()] = MatchingDiscoveryIntent("legacy-intent", "queued")
    payload = {**prepared(), "evidence_fingerprint": "revision-two"}
    result = accept(store, payload)
    assert result["intent_id"] != "legacy-intent"
    assert accept(store, payload) == result
    assert store.enqueued == [result["intent_id"]]
    assert store.intents[choice()].id == "legacy-intent"


def test_postgres_discovery_acceptance_route_keeps_idempotency_key(monkeypatch):
    from app import command_dispatch, discovery_commands, main, postgres_store
    from app.command_gateway import request_digest
    from app.models import DiscoveryAcceptanceRequest
    calls = []
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(discovery_commands, "prepare_discovery_acceptance", lambda *_args: prepared())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key, background=False:
                        calls.append((name, payload, idempotency_key, background))
                        or {"status": "preparing", "intent_id": "intent-one"})
    request = DiscoveryAcceptanceRequest(selected_move_uci="e2e4", evidence_fingerprint="revision-one")
    assert main.accept_discovery_continuation("opening-one", request, "accept-one") == {
        "status": "preparing", "intent_id": "intent-one"}
    assert calls == [("discovery.accept", prepared(), "accept-one", False)]
    changed = {**prepared(), "recommendation": {"move_uci": "e2e4", "preview_moves_uci": ["e2e4", "e7e5"]}}
    assert request_digest("discovery.accept", prepared()) == request_digest("discovery.accept", changed)


def test_discovery_command_composes_borrowed_store_and_preserves_http_error(monkeypatch):
    from fastapi import HTTPException
    from app import discovery_commands
    database = object()
    store = active_store(card_id="saved")
    received = []
    monkeypatch.setattr(discovery_commands, "PostgresDiscoveryAcceptanceStore",
                        lambda connection: received.append(connection) or store)
    with pytest.raises(HTTPException) as error:
        discovery_commands.accept_discovery(database, prepared())
    assert received == [database]
    assert (error.value.status_code, error.value.detail) == (
        409, "This discovery already has a saved decision card")


@pytest.mark.parametrize("replay", [True, False])
def test_discovery_preparation_closes_read_before_recommendation(monkeypatch, replay):
    from contextlib import contextmanager
    from app import discovery_commands
    store = active_store()
    if replay:
        store.intents[choice()] = MatchingDiscoveryIntent("intent-one", "queued")
    inside_read = False
    database = object()
    @contextmanager
    def read_connection():
        nonlocal inside_read
        inside_read = True
        yield database
        inside_read = False
    def recommend(opportunity_id):
        assert not inside_read and opportunity_id == "opening-one"
        assert not replay
        return {"state": "ready", "evidence_fingerprint": "revision-one",
                "repertoire_id": "white-openings", "starting_fen": prepared()["starting_fen"],
                "candidates": [prepared()["recommendation"]]}
    monkeypatch.setattr(discovery_commands, "read_connection", read_connection)
    monkeypatch.setattr(discovery_commands, "PostgresDiscoveryAcceptanceStore", lambda connection: store)
    monkeypatch.setattr(discovery_commands, "recommend_missing_continuations", recommend)
    result = discovery_commands.prepare_discovery_acceptance("opening-one", "e2e4", "revision-one")
    assert result == ({key: prepared()[key] for key in (
        "opportunity_id", "selected_move_uci", "evidence_fingerprint")} if replay else prepared())
    assert not inside_read
