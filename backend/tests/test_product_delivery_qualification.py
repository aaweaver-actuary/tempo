"""Retained outbox policy and local foreground handoff, with no live resources."""
from dataclasses import dataclass
from threading import Event
from types import SimpleNamespace
from contextlib import contextmanager
import uuid

import pytest

from app.contracts.product_v02.games import GameRecord, GameWindow
from app.contracts.product_v02.learning import (EncounterProgress, EncounterReceipt,
    EncounterTarget, GameEffectControl, GameEffectKey, GameEncounter, GameLapse)
from app.contracts.product_v02.repertoire import IntroductionGameScope
from app.games.feedback.coordinator import Coordinator, delivery_for
from app.games.feedback.first_boundary import select_first_boundary
from app.games.feedback.jobs import FeedbackRunner
from app.games.feedback.ports import Delivery, DeliveryDeferred, SourceState
from app.games.feedback.qualification import qualify_delivery
from app.games.feedback.review import SyncConfiguration
from app.product_composition.game_sync import GameSyncRuntime
from app.services.redis_admission_gate import BackgroundAdmissionDeferred
from test_product_feedback_jobs import FakeClock, MemoryStore
from test_product_first_boundary import NOW, game, snapshot
from test_product_game_lapse_postgres import owned_postgres


def command(current, authored, ply=1, *, matched=False):
    decision = next(item for item in authored.decision_presentations if item.route_ply == ply - 1)
    return GameEncounter(key=GameEffectKey(workspace_id=current.workspace_id,
        provider="lichess", game_id=current.game_id, decision_id=decision.decision_id, occurrence_ply=ply),
        source=IntroductionGameScope(provider="lichess", game_id=current.game_id,
            source_revision=current.source_revision, source_digest=current.source_digest),
        decision=decision, expected_uci=decision.selected_response_uci,
        played_uci=decision.selected_response_uci if matched else current.moves[ply - 1],
        observation="matched" if matched else "missed", detected_at=NOW,
        occurred_at=current.played_at, payload_digest=current.source_digest)


def setup_case(moves=("e2e4", "e7e5", "d2d4"), *, introduced=False):
    current = game(moves)
    authored = snapshot(introduction="introduced" if introduced else "unintroduced")
    original = command(current, authored, 3 if len(moves) >= 3 else 1)
    configuration = SyncConfiguration(workspace_id=current.workspace_id, provider="lichess",
        account="Learner", window=GameWindow(start_at="2026-10-25T00:00:00Z",
            end_at="2026-11-01T00:00:00Z", precision="second"), repertoire_ids=("rep",),
        speeds=("blitz",), rated=None, interval_seconds=60, revision=7)
    source = SourceState(source_key="retained-source", original=GameRecord(source_game_id="provider-id",
        source_revision=1, source_digest=current.source_digest, pgn="retained-original-pgn",
        played_at=current.played_at, time_precision="second"), record_fingerprint="original-record",
        normalized=current, findings=(), comparison_revision="original-comparison", effects=(original,))
    return SimpleNamespace(game=current, snapshot=authored, command=original,
        configuration=configuration, source=source)


def qualify(case, original=None, *, unavailable=(), configuration=None, snapshots=None):
    original = original or delivery_for(case.source.source_key, case.command)
    return qualify_delivery(original, source=case.source,
        configuration=configuration or case.configuration,
        snapshots=(case.snapshot,) if snapshots is None else snapshots,
        unavailable=unavailable, determined_at=NOW, retry_at=42,
        select_first_boundary=select_first_boundary)


def test_feedback_enable_qualifies_original_collection_outbox():
    case = setup_case()
    actual = delivery_for(case.source.source_key, case.command)
    matched = delivery_for(case.source.source_key, command(case.game, case.snapshot, 1, matched=True))
    wrong_alternative = snapshot(("d2d4",), repertoire="d4", route_id="d4")
    alternate = delivery_for(case.source.source_key, command(case.game, wrong_alternative, 1))
    before = {item.operation_id: item.command.model_dump_json() for item in (actual, matched, alternate)}
    states = [qualify(case, item) for item in (actual, matched, alternate)]
    assert [item.qualification_status for item in states] == ["ready", "ineligible", "ineligible"]
    assert {item.operation_id: item.command.model_dump_json() for item in states} == before
    assert states[0].qualification.configuration_revision == 7
    assert states[0].qualification.selection.ply == 3


def test_opponent_first_gap_suppresses_retained_later_own_command():
    case = setup_case(("e2e4", "c7c5", "d2d4"))
    result = qualify(case)
    assert result.state == "failed" and result.qualification_status == "ineligible"
    assert result.qualification_reason == "first_departure_opponent_gap"


@pytest.mark.parametrize("missing", ["unavailable", "partial"])
def test_unavailable_scope_is_preserved_until_reconciled(missing):
    case = setup_case()
    configuration = case.configuration.model_copy(update={"repertoire_ids": ("rep", "deferred")})
    absent = ("deferred",)
    result = qualify(case, configuration=configuration, unavailable=absent)
    assert result.qualification_status == "held" and result.state == "pending"
    assert result.qualification is None and result.next_attempt_at == 42
    recovered = qualify(case)
    assert recovered.qualification_status == "ready" and recovered.command == case.command


def test_source_or_target_change_does_not_relabel_frozen_payload():
    case = setup_case()
    prior = delivery_for(case.source.source_key, case.command)
    case.source = case.source.model_copy(update={"normalized": case.game.model_copy(update={"source_revision": 2})})
    result = qualify(case, prior)
    assert result.qualification_status == "held" and result.qualification_reason == "first_departure_source_changed"
    assert result.command.model_dump_json() == prior.command.model_dump_json()


def test_recomparison_does_not_create_policy_version_effect_or_restart_clock():
    case = setup_case()
    result = qualify(case)
    repeated = qualify_delivery(result, source=case.source, configuration=case.configuration,
        snapshots=(case.snapshot,), determined_at="2026-11-03T00:00:00Z", retry_at=99,
        select_first_boundary=select_first_boundary)
    assert repeated.operation_id == result.operation_id and repeated.command == result.command
    assert repeated.determined_at == repeated.qualification.determined_at == NOW


def test_retained_legacy_lapse_has_compatible_or_terminal_disposition():
    from app.games.feedback.comparison import compare_game
    case = setup_case(introduced=True)
    # Historical lapse proof remains its original payload; the new policy does
    # not invent that proof or replace the logical event during qualification.
    finding = compare_game(case.game, provider="lichess", snapshot=case.snapshot)[-1]
    finding = finding.model_copy(update={"kind": "confirmed_miss", "introduced_before_game": True,
        "lapse_eligible": True, "diagnostic": None})
    lapse = GameLapse(key=case.command.key, finding=finding, detected_at=NOW,
        occurred_at=case.game.played_at, payload_digest=case.game.source_digest)
    original = delivery_for(case.source.source_key, lapse)
    result = qualify(case, original)
    assert result.qualification_status == "ready" and result.lapse == lapse and result.encounter is None
    case.source = case.source.model_copy(update={"quarantined": True})
    assert qualify(case, original).qualification_status == "held"


class QualifiedReceiver:
    def __init__(self, *, lose_ack=False, defer=False):
        self.receipts, self.calls = {}, []
        self.lose_ack, self.defer = lose_ack, defer

    def accept_qualified(self, original, *, qualification):
        self.calls.append((original, qualification))
        if self.defer:
            raise DeliveryDeferred("current_source_busy", retry_after=12)
        identity = original.key.logical_id()
        if identity not in self.receipts:
            source = original.source if isinstance(original, GameEncounter) else qualification.selection.source
            self.receipts[identity] = EncounterReceipt(interface_version="0.2.2", key=original.key,
                source_revision=source.source_revision, status="pending", outcome="accepted",
                owner_receipt_id="same-owner-receipt", admitted_at=None, available_by=None, applied_at=None,
                reason="study_priority_pending", detected_at=original.detected_at,
                source_digest=source.source_digest, observation="missed", observation_recorded=True,
                disposition="first_introduction", admission_status="pending", deferred_reason=None,
                progress=EncounterProgress(determined_at=qualification.determined_at,
                    stream=qualification.stream, target=EncounterTarget(
                        card_id=qualification.selection.decision.presentation_id, card_revision=1),
                    scheduling_review_id=None, schedule_applied_at=None, introduction_applied_at=None,
                    remediation_fence=None, first_issued=None, first_issued_ordinal=None, settlement=None))
            if self.lose_ack:
                self.lose_ack = False
                raise TimeoutError("owner_commit_before_ack")
        return self.receipts[identity]


def coordinator_case(*, lose_ack=False, defer=False):
    case = setup_case()
    store, clock = MemoryStore(), FakeClock()
    original = delivery_for(case.source.source_key, case.command)
    store.deliveries[original.operation_id] = original
    receiver = QualifiedReceiver(lose_ack=lose_ack, defer=defer)
    qualifier = SimpleNamespace(prepare=lambda delivery: qualify(case, delivery))
    coordinator = Coordinator(providers={}, normalizer=None, comparer=None, snapshots=None,
        store=store, learning=receiver, clock=clock, qualifier=qualifier)
    return case, store, clock, receiver, coordinator, original


def test_ack_loss_replays_same_effect_once():
    case, store, clock, receiver, coordinator, original = coordinator_case(lose_ack=True)
    coordinator.deliver(original)
    retry = store.delivery(original.operation_id)
    assert retry.error == "owner_acknowledgment_uncertain" and retry.attempts == 1
    coordinator.deliver(retry)
    actual = store.delivery(original.operation_id)
    assert actual.state == "acknowledged" and len(receiver.receipts) == 1
    assert all(item[0] == original.command for item in receiver.calls)
    assert actual.determined_at == NOW and actual.receipt.progress.determined_at == NOW


def test_semantic_hold_is_not_an_unsupported_command_transport_retry():
    case, store, clock, receiver, coordinator, original = coordinator_case(defer=True)
    coordinator.deliver(original)
    actual = store.delivery(original.operation_id)
    assert actual.qualification_status == "held" and actual.error == "current_source_busy"
    assert actual.attempts == 0 and actual.next_attempt_at == clock.now() + 12
    assert actual.command == original.command and actual.determined_at == NOW


def test_control_wins_between_qualification_and_intake():
    case, store, clock, receiver, coordinator, original = coordinator_case()
    def prepare(delivery):
        store.deliveries[delivery.operation_id] = delivery.model_copy(update={"state": "superseded"})
        return qualify(case, delivery)
    coordinator.qualifier = SimpleNamespace(prepare=prepare)
    coordinator.deliver(original)
    assert store.delivery(original.operation_id).state == "superseded" and receiver.calls == []


def test_continuous_foreground_advances_determined_but_unprepared_effect():
    case, store, clock, receiver, coordinator, original = coordinator_case()
    ready = qualify(case, original)
    store.deliveries[ready.operation_id] = ready
    def pending(now, *, ready_only=False):
        assert ready_only
        return next((item for item in store.deliveries.values() if item.state == "pending"), None)
    store.pending_delivery = pending
    store.claim_job = lambda *args: pytest.fail("local handoff must not claim/fetch provider work")
    @contextmanager
    def foreground():
        raise BackgroundAdmissionDeferred("foreground remains active")
        yield
    @contextmanager
    def local():
        yield
    runtime = GameSyncRuntime(FeedbackRunner(coordinator), Event(), admission=foreground, local_admission=local)
    assert runtime.run_once() == "delivery"
    actual = store.delivery(ready.operation_id)
    assert actual.state == "acknowledged" and actual.determined_at == NOW
    assert receiver.calls[0][1].determined_at == NOW


def test_collection_only_local_handoff_never_touches_pending_state():
    case, store, clock, receiver, coordinator, original = coordinator_case()
    store.pending_delivery = lambda *args, **kwargs: pytest.fail("collection-only queried pending")
    assert FeedbackRunner(coordinator, delivery_enabled=False).run_delivery_once() == "idle"
    assert store.delivery(original.operation_id) == original and receiver.calls == []


def test_mode_admission_is_required_without_worker_health_dependency():
    from app.product_composition.provider_priority import pending_determined_feedback, advance_determined_feedback
    denied = lambda: False
    forbidden = lambda: pytest.fail("no Games read without verified feedback intent")
    assert not pending_determined_feedback(workspace="fixture", connections=forbidden, admitted=denied)
    result = advance_determined_feedback(workspace="fixture", connections=forbidden,
        clock=FakeClock(), admitted=denied)
    assert not result.progressed and not result.pending and result.held_reason == "feedback_mode_not_admitted"


@pytest.fixture
def native_deliveries(owned_postgres):
    from psycopg.types.json import Jsonb
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    workspace = "qualification-" + uuid.uuid4().hex
    @contextmanager
    def connections():
        with owned_postgres() as database:
            yield database.raw
    store = PostgresGamesStore(connections)
    def seed(stream, ordinal):
        case = setup_case()
        identity = uuid.uuid4().hex
        current = case.game.model_copy(update={"workspace_id": workspace, "game_id": identity})
        original = case.command.model_copy(update={"key": case.command.key.model_copy(
            update={"workspace_id": workspace, "game_id": identity}),
            "source": case.command.source.model_copy(update={"game_id": identity})})
        case.game, case.command = current, original
        case.configuration = case.configuration.model_copy(update={"workspace_id": workspace})
        case.source = case.source.model_copy(update={"source_key": identity, "version": 1,
            "normalized": current, "effects": (original,)})
        # Scope identity belongs to this synthetic workspace, as do the cards.
        case.snapshot = case.snapshot.model_copy(update={"membership": case.snapshot.membership.model_copy(
            update={"workspace_id": workspace})})
        delivery = qualify(case, delivery_for(identity, original).model_copy(update={"stream": stream}))
        delivery = delivery.model_copy(update={"determined_at": f"2026-10-30T00:00:{ordinal:02d}Z",
            "qualification": delivery.qualification.model_copy(update={
                "determined_at": f"2026-10-30T00:00:{ordinal:02d}Z"})})
        with connections() as database:
            database.execute("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,1,%s)",
                (identity, Jsonb(case.source.model_dump(mode="json"))))
            database.execute("""INSERT INTO product_feedback_deliveries
                (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload)
                VALUES(%s,%s,%s,'encounter','pending',0,%s)""",
                (delivery.operation_id, identity, original.key.logical_id(), Jsonb(delivery.model_dump(mode="json"))))
        return delivery
    return SimpleNamespace(store=store,connections=connections,workspace=workspace,seed=seed)


def test_native_live_and_history_deliveries_have_bounded_fair_progress(native_deliveries):
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    case = native_deliveries
    live = [case.seed("forward", ordinal) for ordinal in (1, 3, 5)]
    history = [case.seed("reconciliation", ordinal) for ordinal in (2, 4)]
    seen = []
    # Equal attempt clocks cannot reset the cursor; each fresh store reads the
    # actual persisted service sequence, not an in-process quota counter.
    for _ in range(5):
        restored = PostgresGamesStore(case.connections)
        selected = restored.pending_delivery(100, workspace=case.workspace)
        seen.append(selected.operation_id)
        restored.save_delivery(selected.model_copy(update={"attempts": selected.attempts + 1,
            "next_attempt_at": 200}))
    assert seen == [live[0].operation_id, history[0].operation_id,
        live[1].operation_id, history[1].operation_id, live[2].operation_id]
    with case.connections() as database:
        rows = database.execute("""SELECT payload::text FROM product_feedback_deliveries
            WHERE source_key=ANY(%s)""", ([item.source_key for item in live + history],)).fetchall()
    retained = [Delivery.model_validate_json(row[0]) for row in rows]
    assert sorted(item.service_sequence for item in retained) == [1, 2, 3, 4, 5]
    assert all(item.command == next(original.command for original in live + history
        if original.operation_id == item.operation_id) for item in retained)


def test_native_controls_win_after_qualification_and_delayed_ack(native_deliveries):
    from app.games.feedback.ports import JobSpec, PageCommit
    case = native_deliveries
    original = case.seed("forward", 1)
    control = delivery_for(original.source_key, GameEffectControl(key=original.command.key,
        source_revision=1, action="hold", reason="reanalysis"))
    request = JobSpec(job_id=uuid.uuid4().hex, workspace_id=case.workspace, provider="lichess",
        account="Learner", window=setup_case().configuration.window, repertoire_ids=("rep",))
    job = case.store.enqueue(request)
    case.store.commit_page(PageCommit(job=job, sources=(), captures=(), deliveries=(control,)))
    selected = case.store.pending_delivery(100, workspace=case.workspace)
    assert selected.control == control.control
    case.store.save_delivery(original.model_copy(update={"state": "acknowledged", "attempts": 1}))
    retained = case.store.delivery(original.operation_id)
    assert retained.state == "superseded" and retained.command == original.command
    changed = retained.model_copy(update={"encounter": retained.encounter.model_copy(update={"payload_digest": "b" * 64})})
    with pytest.raises(ValueError, match="captured delivery command cannot be replaced"):
        case.store.save_delivery(changed)
    assert case.store.delivery(original.operation_id).command == original.command


def test_native_nonretryable_held_delivery_is_not_a_transport_loop(native_deliveries):
    case = native_deliveries
    original = case.seed("forward", 1)
    held = original.model_copy(update={"qualification_status": "held",
        "qualification_reason": "first_departure_source_changed", "qualification_retryable": False})
    case.store.save_delivery(held)
    assert case.store.pending_delivery(100, workspace=case.workspace) is None
    assert case.store.delivery(original.operation_id).attempts == 0

