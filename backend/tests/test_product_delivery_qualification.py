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


def test_new_occurrences_share_target_but_not_effect_identity():
    case = setup_case()
    first = qualify(case)
    later_game = case.game.model_copy(update={"game_id": "another-genuine-game"})
    later_command = case.command.model_copy(update={"key": case.command.key.model_copy(
        update={"game_id": later_game.game_id}), "source": case.command.source.model_copy(
        update={"game_id": later_game.game_id})})
    case.game, case.command = later_game, later_command
    case.source = case.source.model_copy(update={"normalized": later_game,
        "source_key": "another-source", "effects": (later_command,)})
    second = qualify(case)
    assert first.command.key != second.command.key and first.operation_id != second.operation_id
    assert first.qualification.selection.decision.presentation_id == second.qualification.selection.decision.presentation_id


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


def test_settled_legacy_ack_loss_recovers_actual_owner_receipt_without_new_progress_or_reset():
    from datetime import timedelta
    from app.study.game_feedback import Completion
    from app.study.encounter_feedback import EncounterFeedback
    from app.games.feedback.qualification import acknowledge_qualified_delivery
    from test_product_game_lapse import (owner,fixture,NOW as STAMP,CARD,issued_attempt,qualification_for)
    store,decisions,clock,legacy = owner.__wrapped__()
    original = fixture("lapse",GameLapse)
    legacy.accept_lapse(original)
    completion = Completion(100,CARD,1,STAMP+timedelta(hours=1),True,"study",
        issued_attempt(CARD,1,8,STAMP+timedelta(minutes=1)))
    clock[0] = completion.reviewed_at
    legacy.settle(completion)
    witness = qualification_for(original)
    current = EncounterFeedback(store,decisions,clock=lambda:clock[0],introductions=None,introduction_policy=None)
    receipt = current.accept_qualified(original,qualification=witness)
    assert receipt.status == "settled" and receipt.interface_version == "0.2.1" and receipt.progress is None
    delivery = delivery_for("original-source",original).model_copy(update={"qualification_status":"ready",
        "qualification":witness,"determined_at":witness.determined_at,"stream":witness.stream})
    acknowledged = acknowledge_qualified_delivery(delivery,receipt)
    assert acknowledged.state == "acknowledged" and acknowledged.command == original
    assert acknowledged.receipt == receipt and len(store.reviews) == len(store.effects) == 1


def test_historical_held_legacy_ack_loss_recovers_controlled_owner_without_new_reset():
    from app.study.encounter_feedback import EncounterFeedback
    from app.games.feedback.qualification import acknowledge_qualified_delivery
    from test_product_game_lapse import owner,fixture,qualification_for
    store,decisions,clock,legacy = owner.__wrapped__()
    original = fixture("lapse",GameLapse)
    legacy.accept_lapse(original)
    control = GameEffectControl(key=original.key,source_revision=original.finding.source_revision,
        action="hold",reason="reanalysis")
    legacy.control_effect(control)
    witness = qualification_for(original)
    current = EncounterFeedback(store,decisions,clock=lambda:clock[0],introductions=None,introduction_policy=None)
    receipt = current.accept_qualified(original,qualification=witness)
    assert receipt.status == "held" and receipt.interface_version == "0.2.1" and receipt.progress is None
    assert receipt.outcome == "historical_preserved"
    delivery = delivery_for("original-source",original).model_copy(update={"qualification_status":"ready",
        "qualification":witness,"determined_at":witness.determined_at,"stream":witness.stream})
    acknowledged = acknowledge_qualified_delivery(delivery,receipt)
    assert acknowledged.state == "acknowledged" and acknowledged.command == original
    assert store.get(original.key).control == control and len(store.reviews) == len(store.effects) == 1


@pytest.mark.parametrize("complete",[True,False])
def test_conclusive_reanalysis_controls_accepted_effect_but_missing_proof_preserves_history(complete):
    from test_product_feedback_jobs import encounter_harness,spec
    h = encounter_harness()
    h.store.enqueue(spec()); assert h.runner.run_once() == "page"
    assert h.runner.run_once() == "delivery"
    original, = h.store.deliveries.values()
    assert original.state == "acknowledged"
    authored = snapshot(("d2d4",),repertoire="white",route_id="now-covered")
    if not complete:
        authored = authored.model_copy(update={"membership":authored.membership.model_copy(
            update={"complete":False,"available":False})})
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda _:authored)
    h.coordinator.comparer = SimpleNamespace(compare=lambda *args,**kwargs:())
    h.store.enqueue(spec("reanalysis")); assert FeedbackRunner(h.coordinator).run_once() == "page"
    controls = [item for item in h.store.deliveries.values() if item.control is not None]
    assert len(controls) == int(complete)
    if controls:
        assert controls[0].control.key == original.command.key and controls[0].control.action == "hold"
    source, = h.store.sources.values()
    assert source.effects == (original.command,) and h.store.delivery(original.operation_id).receipt == original.receipt


def test_changed_authored_response_same_logical_id_controls_original_accepted_effect():
    from test_product_feedback_jobs import encounter_harness,spec
    h = encounter_harness()
    h.store.enqueue(spec()); assert h.runner.run_once() == "page"
    assert h.runner.run_once() == "delivery"
    original, = h.store.deliveries.values()
    authored = snapshot(("c2c4",),repertoire="white",route_id="changed-response")
    changed = authored.decision_presentations[0].model_copy(update={
        "decision_id":original.command.key.decision_id,"presentation_id":original.command.decision.presentation_id,
        "presentation_revision":2})
    authored = authored.model_copy(update={"decision_presentations":(changed,)})
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda _:authored)
    h.coordinator.comparer = SimpleNamespace(compare=lambda *args,**kwargs:())
    h.store.enqueue(spec("changed-authored")); assert FeedbackRunner(h.coordinator).run_once() == "page"
    control, = [item for item in h.store.deliveries.values() if item.control is not None]
    source, = h.store.sources.values()
    assert source.first_departure.status == "own_actionable" and source.first_departure.expected_uci == ("c2c4",)
    assert control.command.key == original.command.key and control.control.action == "hold"
    assert h.store.delivery(original.operation_id).command == original.command


def test_corrected_repertoire_scope_recompares_retained_source_without_quarantine_or_old_clock():
    from app.games.feedback.comparison import compare_game
    from test_product_feedback_jobs import encounter_harness, spec
    h = encounter_harness()
    normalize = h.coordinator.normalizer.normalize
    h.coordinator.normalizer = SimpleNamespace(normalize=lambda original, request:
        normalize(original, request).model_copy(update={"moves": ["d2d4", "d7d5", "c2c4"]}))
    old = snapshot(repertoire="white", route_id="old-e4")
    corrected = snapshot(("d2d4", "d7d5", "g1f3"), repertoire="london", route_id="london")
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda _: old)
    h.coordinator.comparer = SimpleNamespace(compare=lambda current, authored, **kwargs:
        compare_game(current, provider="lichess", snapshot=authored))
    h.store.enqueue(spec())
    assert h.runner.run_once() == "page"
    original_source, = h.store.sources.values()
    original, = h.store.deliveries.values()
    assert original.command.key.occurrence_ply == 1
    h.clock.value += 60
    h.coordinator.snapshots = SimpleNamespace(snapshot=lambda repertoire:
        corrected if repertoire == "london" else old)
    request = spec("corrected-scope").model_copy(update={"repertoire_ids": ("white", "london")})
    h.store.enqueue(request)
    h.coordinator.capture_page(h.store.load_job(request.job_id))
    current, = h.store.sources.values()
    assert not current.quarantined
    assert current.original == original_source.original and current.normalized == original_source.normalized
    assert current.first_departure.status == "own_actionable" and current.first_departure.ply == 3
    assert current.determined_at == h.clock.utc_now() != original_source.determined_at
    assert h.store.delivery(original.operation_id).command == original.command
    assert h.store.delivery(original.operation_id).state == "superseded"
    later, = [item for item in h.store.deliveries.values()
        if item.encounter is not None and item.command.key.occurrence_ply == 3]
    assert later.command.decision.presentation_id == "card-london" and later.state == "pending"
    assert len(h.store.captures) == 2
    repeated = request.model_copy(update={"job_id": "same-corrected-scope"})
    h.clock.value += 60
    h.store.enqueue(repeated)
    h.coordinator.capture_page(h.store.load_job(repeated.job_id))
    replayed, = h.store.sources.values()
    assert not replayed.quarantined and replayed.determined_at == current.determined_at
    assert h.store.delivery(later.operation_id).command == later.command
    assert len(h.store.captures) == 2


def test_unqualified_concrete_game_review_fallback_cannot_call_legacy_scheduler():
    from app.product_composition.game_review import create_game_review
    from app.games.feedback.review import ReviewUnavailable
    case = setup_case()
    learning = SimpleNamespace(accept_lapse=lambda *_:pytest.fail("old scheduler called"))
    review = create_game_review(workspace="fixture",connections=lambda:pytest.fail("Games writer opened"),
        learning=learning,clock=FakeClock())
    with pytest.raises(ReviewUnavailable,match="qualified_provider_encounter_receiver_required"):
        review.deliver(delivery_for(case.source.source_key,case.command))


@pytest.mark.parametrize("fault",["nonselected","changed_boot"])
def test_public_legacy_retry_rejects_unsafe_preparation_before_any_writer(monkeypatch,fault):
    from fastapi import HTTPException
    from app.product_composition import game_review_commands as commands,provider_priority,managed_runtime
    from test_product_game_lapse import fixture
    original = fixture("lapse",GameLapse)
    delivery = delivery_for("original-source",original)
    source = SimpleNamespace(source_key=delivery.source_key,quarantined=False,findings=(original.finding,))
    class Reads:
        def __init__(self, connections): pass
        def source_detail(self,*args): return source
        def deliveries(self,*args): return (delivery,)
    calls = []
    def qualify_selected(selected,**kwargs):
        calls.append(selected.command)
        rejected = selected.model_copy(update={"state":"failed","qualification_status":"ineligible",
            "qualification_reason":"first_departure_opponent_gap"})
        return provider_priority.PreparedDeterminedFeedback(selected,rejected,
            None if fault == "nonselected" else object(),"trusted-boot" if fault == "nonselected" else "previous-boot")
    monkeypatch.setattr(commands,"PostgresReviewStore",Reads)
    monkeypatch.setattr(provider_priority,"prepare_delivery_feedback",qualify_selected)
    monkeypatch.setattr(managed_runtime,"current_managed_runtime_identity",lambda:"trusted-boot")
    with pytest.raises(HTTPException) as held:
        commands.prepare_retry(dict(finding=original.finding.model_dump(mode="json"),operation_id="retry-original"),
            connections=lambda:pytest.fail("retry opened a writer"),clock=FakeClock())
    assert held.value.status_code == (409 if fault == "nonselected" else 503)
    assert held.value.detail == ("first_departure_opponent_gap" if fault == "nonselected" else "verified_feedback_mode_required")
    assert calls == [original] and delivery.state == "pending"


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


def test_qualification_decodes_complete_publication_after_reader_exit(monkeypatch):
    import json
    from app.product_composition import provider_priority as composition
    from tempo_legacy_database.adapters.authored_repertoires import product_snapshot
    from tempo_legacy_database.adapters.games import product_review
    case, reading, events = setup_case(), [False], []
    @contextmanager
    def connections():
        reading[0] = True
        try:
            yield object()
        finally:
            reading[0] = False
            events.append("closed")
    class Reads:
        def __init__(self, connections): pass
        def configuration(self, *args): return case.configuration
        def source_detail(self, *args): return case.source
    def raw_read(database, repertoire):
        assert reading[0] and repertoire == "rep"
        events.append("raw")
        return '{"complete":"publication"}'
    def decode(text):
        assert not reading[0]
        events.append("decode")
        return json.loads(text)
    def project(facts):
        assert not reading[0] and facts == {"complete": "publication"}
        events.append("project")
        return case.snapshot
    monkeypatch.setattr(product_review, "PostgresReviewStore", Reads)
    monkeypatch.setattr(product_snapshot, "read_repertoire_json", raw_read)
    monkeypatch.setattr(composition, "json", SimpleNamespace(loads=decode))
    monkeypatch.setattr(composition, "project_published_repertoire", project)
    result = composition._qualified_facts(case.command, connections=connections,
        clock=FakeClock(), delivery=delivery_for(case.source.source_key,case.command),
        target_prepare=lambda *args,**kwargs:None)
    assert result[0].qualification_status == "ready"
    assert events == ["raw", "closed", "decode", "project"]


@pytest.mark.parametrize("binding",["none","valid","identity_change","hold"])
def test_current_target_enrichment_requires_explicit_owner_and_keeps_complete_publication(binding):
    from app.product_composition.provider_priority import _current_feedback_targets
    case = setup_case()
    original = case.command.decision.model_copy(update={"available":False})
    authored = case.snapshot.model_copy(update={"decision_presentations":tuple(
        original if item.decision_id == original.decision_id else item
        for item in case.snapshot.decision_presentations)})
    frozen, calls = case.command.model_dump_json(), []
    def prepare(mapping, *, read_connection):
        calls.append(mapping)
        assert mapping == original and callable(read_connection)
        if binding == "hold":
            raise ValueError("feedback_target_current_unavailable")
        if binding == "none":
            return None
        current = mapping.model_copy(update={"presentation_id":"explicit-current-presentation",
            "presentation_revision":2,"available":True})
        if binding == "identity_change":
            current = current.model_copy(update={"decision_id":"different-logical-decision"})
        return SimpleNamespace(original=mapping,current=current)
    def resolve():
        return _current_feedback_targets(case.command,(authored,),
            connections=lambda:pytest.fail("projection opened a connection"),target_prepare=prepare)
    if binding in {"hold","identity_change"}:
        with pytest.raises(DeliveryDeferred) as held:
            resolve()
        assert held.value.reason == ("first_departure_target_lineage_unavailable"
            if binding == "hold" else "first_departure_target_lineage_unproved")
    else:
        (resolved,),targets = resolve()
        assert resolved.membership == authored.membership and resolved.boundaries == authored.boundaries
        assert resolved.decision_presentations[:-1] == authored.decision_presentations[:-1]
        selected = select_first_boundary(case.game,snapshots=(resolved,),source=case.command.source)
        assert selected.status == ("own_actionable" if binding == "valid" else "own_unmapped")
        assert bool(targets) is (binding == "valid")
        if targets:
            assert selected.decision.presentation_id == targets[0].current.presentation_id
    assert calls == [original] and case.command.model_dump_json() == frozen


@pytest.fixture
def native_handoff(owned_postgres, monkeypatch,request):
    """Real publication/source, immutable outbox, mode and Study owner join."""
    import hashlib
    import chess
    import chess.pgn
    from datetime import timedelta
    from product_encounter_fixtures import retained_history_case
    from app.product_composition.retained_game import retained_canonical_game
    from app.product_composition import provider_priority as composition
    from app.product_composition import managed_runtime
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    from tempo_legacy_database.adapters.games.managed_mode import ManagedGamesModeStore
    from test_game_feedback_mode import intent, RUNTIME
    from test_product_game_lapse import NOW as STAMP
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(managed_runtime, "current_managed_runtime_identity", lambda: RUNTIME)
    legacy = getattr(request,"param",None) == "lapse"
    with retained_history_case(owned_postgres, state="introduced" if legacy else "unintroduced", ordinary_limit=0, study_only=True) as case:
        @contextmanager
        def connections():
            with case.transaction() as database:
                database.execute_native("""SELECT set_config('transaction_timeout','250ms',true),
                    set_config('lock_timeout','100ms',true),set_config('statement_timeout','250ms',true)""")
                yield database.raw
        @contextmanager
        def writer():
            with case.transaction() as database:
                database.execute_native("""SELECT set_config('transaction_timeout','250ms',true),
                    set_config('lock_timeout','100ms',true),set_config('statement_timeout','250ms',true)""")
                yield database
        connections.writer = writer
        captured = case.capture(case.game_ids[0])
        original, canonical = case.command(captured), retained_canonical_game(captured)
        if legacy:
            from app.games.feedback.comparison import compare_game
            from app.repertoire import project_capture
            finding, = compare_game(canonical,provider="lichess",time_precision="second",
                snapshot=project_capture(captured,comparison_game=canonical))
            assert finding.kind == "confirmed_miss"
            original = GameLapse(key=original.key,finding=finding,detected_at=original.detected_at,
                occurred_at=original.occurred_at,payload_digest=original.payload_digest)
        pgn = chess.pgn.Game(); pgn.setup(chess.Board(canonical.starting_fen))
        pgn.add_variation(chess.Move.from_uci(original.finding.played_uci if legacy else original.played_uci))
        text = str(pgn)
        record = GameRecord(source_game_id=captured["game"]["provider_game_id"],source_revision=1,
            source_digest=hashlib.sha256(text.encode()).hexdigest(),pgn=text,
            played_at=canonical.played_at,time_precision="second")
        source = SourceState(source_key=case.card_id+"-source",version=1,original=record,
            record_fingerprint=record.source_digest,normalized=canonical,findings=(original.finding,) if legacy else (),
            comparison_revision="original-capture",effects=(original,),encounters=() if legacy else (original,))
        config = SyncConfiguration(workspace_id=canonical.workspace_id,provider="lichess",account="synthetic",
            window=GameWindow(start_at=(STAMP-timedelta(days=2)).isoformat(),end_at=STAMP.isoformat(),
                precision="second"),repertoire_ids=(case.repertoire_id,),speeds=("rapid",),rated=None,
            interval_seconds=None)
        PostgresReviewStore(connections).configure(config,0)
        modes = ManagedGamesModeStore(connections)
        modes.record(intent(canonical.workspace_id),expected_generation=0)
        store = PostgresGamesStore(connections)
        capture = lambda repertoire, game, plies: case.capture(game)
        class Clock:
            def now(self): return STAMP.timestamp()
            def utc_now(self): return STAMP.isoformat()
        clock = Clock()
        try:
            with case.transaction() as database:
                database.execute_native("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,1,%s::jsonb)",
                    (source.source_key,source.model_dump_json()))
            proof = composition.prepare_feedback_qualification(original,connections=connections,
                capture=capture,clock=clock,determination=STAMP.isoformat(),stream="forward")
            from app.study.game_feedback import effect_id
            from tempo_legacy_database.adapters.games.product_feedback import handoff_priority_for
            delivery = delivery_for(source.source_key,original).model_copy(update={
                "qualification_status":"ready", "qualification":proof.qualification,
                "determined_at":STAMP.isoformat(),"stream":"forward",
                "owner_effect_id":effect_id(original.key)})
            with case.transaction() as database:
                database.execute_native("""INSERT INTO product_feedback_deliveries
                    (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload,handoff_priority)
                    VALUES(%s,%s,%s,%s,'pending',0,%s::jsonb,%s)""",
                    (delivery.operation_id,source.source_key,original.key.logical_id(),delivery.command_kind,delivery.model_dump_json(),
                     handoff_priority_for(delivery)))
            yield SimpleNamespace(case=case,workspace=canonical.workspace_id,connections=connections,
                store=store,modes=modes,source=source,original=delivery,capture=capture,clock=clock,
                runtime_id=RUNTIME,intent=intent)
        finally:
            with case.transaction() as database:
                import json
                database.execute_native("DELETE FROM product_feedback_deliveries WHERE source_key=%s",(source.source_key,))
                database.execute_native("DELETE FROM product_feedback_sources WHERE source_key=%s",(source.source_key,))
                database.execute_native("DELETE FROM product_feedback_jobs WHERE payload->'spec'->'repertoire_ids'=%s::jsonb",
                    (json.dumps([case.repertoire_id]),))
                database.execute_native("DELETE FROM product_feedback_configuration WHERE workspace_id=%s AND provider='lichess'",
                    (canonical.workspace_id,))
                database.execute_native("DELETE FROM product_feedback_managed_mode WHERE workspace_id=%s",(canonical.workspace_id,))


def test_native_pure_prepare_and_receipt_fenced_apply_commit_both_owners_atomically(native_handoff, monkeypatch):
    from app.product_composition import provider_priority as composition, managed_runtime
    from tempo_legacy_database.adapters.games import product_feedback
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case = native_handoff
    before = case.case.readback()
    def prepare():
        return composition.prepare_determined_feedback(workspace=case.workspace,
            connections=case.connections,clock=case.clock,admitted=lambda:True,capture=case.capture)
    prepared = prepare()
    discarded_for_replayed_gateway_request = prepare()
    assert prepared.original_delivery == discarded_for_replayed_gateway_request.original_delivery == case.original
    assert prepared.provider_feedback is not None
    assert case.store.delivery(case.original.operation_id) == case.original and case.case.readback() == before
    with case.case.transaction() as database:
        assert PostgresGameFeedbackStore(database).get(case.original.command.key) is None
    # The handler already owns its receipt/token fence. These capabilities must
    # never be read or invoked from its borrowed writer.
    monkeypatch.setattr(managed_runtime, "current_managed_runtime_identity",
        lambda:pytest.fail("runtime filesystem read inside writer"))
    def apply(database):
        database.execute_native("SET LOCAL transaction_timeout='250ms'")
        actual_execute = database.raw.execute
        def bounded_execute(statement,*args,**kwargs):
            assert not ("MAX(" in str(statement) and "product_feedback_deliveries" in str(statement)), "Study writer scanned retained service history"
            return actual_execute(statement,*args,**kwargs)
        monkeypatch.setattr(database.raw,"execute",bounded_execute)
        return composition.apply_prepared_determined_feedback(database,prepared,
            clock=case.clock,current_runtime_id=case.runtime_id)
    # A gateway failure AFTER successful source apply rolls back both owners.
    with pytest.raises(RuntimeError,match="gateway_receipt_failure"):
        with case.case.transaction() as database:
            assert apply(database).progressed
            raise RuntimeError("gateway_receipt_failure")
    assert case.store.delivery(case.original.operation_id) == case.original
    with case.case.transaction() as database:
        assert PostgresGameFeedbackStore(database).get(case.original.command.key) is None
    actual_save = product_feedback.save_delivery_in_transaction
    def failed_ack(*args,**kwargs):
        actual_save(*args,**kwargs)
        raise RuntimeError("ack_write_failed_after_owner_accept")
    monkeypatch.setattr(product_feedback,"save_delivery_in_transaction",failed_ack)
    with case.case.transaction() as database:
        failed = apply(database)
        assert not failed.progressed and failed.pending
    monkeypatch.setattr(product_feedback,"save_delivery_in_transaction",actual_save)
    assert case.store.delivery(case.original.operation_id) == case.original
    with case.case.transaction() as database:
        assert PostgresGameFeedbackStore(database).get(case.original.command.key) is None
    with case.case.transaction() as database:
        result = apply(database)
        assert result.progressed and result.pending
    acknowledged = case.store.delivery(case.original.operation_id)
    assert acknowledged.state == "acknowledged" and acknowledged.command == case.original.command
    assert acknowledged.determined_at == acknowledged.receipt.progress.determined_at == case.original.determined_at
    with case.case.transaction() as database:
        assert PostgresGameFeedbackStore(database).get(case.original.command.key) is not None
        assert database.execute_native("SELECT count(*) FROM study_game_feedback WHERE card_id=%s",(case.case.card_id,)).fetchone()[0] == 1
        assert not apply(database).progressed  # Stale prepared original cannot advance another unit.
    assert case.store.delivery(case.original.operation_id) == acknowledged


@pytest.mark.parametrize("path",["worker","gateway"])
def test_native_continuous_foreground_delivers_control_before_accepted_study_work(native_handoff,monkeypatch,path):
    from app.product_composition import provider_priority as composition
    from app.games.feedback.ports import JobSpec,PageCommit
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case = native_handoff
    prepared = composition.prepare_determined_feedback(workspace=case.workspace,connections=case.connections,
        clock=case.clock,admitted=lambda:True,capture=case.capture)
    with case.connections.writer() as database:
        assert composition.apply_prepared_determined_feedback(database,prepared,
            clock=case.clock,current_runtime_id=case.runtime_id).progressed
    control = delivery_for(case.source.source_key,GameEffectControl(key=case.original.command.key,
        source_revision=1,action="hold",reason="reanalysis"))
    spec = JobSpec(job_id=uuid.uuid4().hex,workspace_id=case.workspace,provider="lichess",account="synthetic",
        window=setup_case().configuration.window,repertoire_ids=(case.case.repertoire_id,))
    case.store.commit_page(PageCommit(job=case.store.enqueue(spec),sources=(),captures=(),deliveries=(control,)))
    assert composition.pending_determined_feedback(workspace=case.workspace,connections=case.connections,
        admitted=lambda:True,before_priority=prepared.priority)
    monkeypatch.setattr(composition,"_qualified_facts",lambda *args,**kwargs:pytest.fail("control replayed/projection work"))
    if path == "gateway":
        selected = composition.prepare_determined_feedback(workspace=case.workspace,connections=case.connections,
            clock=case.clock,admitted=lambda:True,capture=case.capture)
        assert selected.is_control and selected.priority is None
        with case.connections.writer() as database:
            result = composition.apply_prepared_determined_feedback(database,selected,
                clock=case.clock,current_runtime_id=case.runtime_id)
            assert result.progressed
    else:
        intake = composition.CurrentProviderPriorityIntake(connections=case.connections,clock=case.clock,
            operation_stopped=lambda:False,admitted=lambda:True,capture=case.capture)
        coordinator = Coordinator(providers={},normalizer=None,comparer=None,snapshots=None,
            store=case.store,learning=intake,clock=case.clock,qualifier=intake)
        case.store.claim_job = lambda *args:pytest.fail("foreground control claimed provider work")
        assert FeedbackRunner(coordinator).run_delivery_once() == "delivery"
    actual = case.store.delivery(control.operation_id)
    assert actual.state == "acknowledged" and actual.command == control.command
    with case.case.transaction() as database:
        effect = PostgresGameFeedbackStore(database).get(case.original.command.key)
        assert effect.status == "held" and effect.control == control.command
        assert effect.encounter == case.original.command


@pytest.mark.parametrize("native_handoff",["lapse"],indirect=True)
def test_native_public_legacy_retry_uses_same_qualified_owner_and_gateway_receipt(native_handoff,monkeypatch):
    from app import command_gateway as gateway
    from app.product_composition import game_review_commands as commands
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case = native_handoff
    operation = "qualified-retry-"+uuid.uuid4().hex
    payload = dict(finding=case.original.lapse.finding.model_dump(mode="json"),operation_id=operation)
    prepare = lambda value:commands.prepare_retry(value,connections=case.connections,clock=case.clock,
        capture=case.capture,workspace=case.workspace)
    selected = prepare(payload)
    assert selected.work.delivery.command == case.original.command
    assert case.store.delivery(case.original.operation_id) == case.original
    monkeypatch.setattr(gateway,"_handlers",{})
    monkeypatch.setattr(gateway,"_preparers",{})
    monkeypatch.setattr(gateway,"_writer_connection",lambda background:case.case.transaction())
    def bounded_retry(database,prepared):
        actual_execute = database.raw.execute
        def bounded_execute(statement,*args,**kwargs):
            sql = str(statement)
            assert not ("product_feedback_deliveries" in sql and "WHERE source_key=" in sql), "retry writer rescanned retained source deliveries"
            return actual_execute(statement,*args,**kwargs)
        monkeypatch.setattr(database.raw,"execute",bounded_execute)
        return commands.retry(database,prepared)
    gateway.register_command("product_games.retry_feedback",bounded_retry,prepare=prepare)
    first = gateway.execute_command(operation,"product_games.retry_feedback",payload)
    assert first is not None and first["interface_version"] == "0.2.2"
    assert first == gateway.execute_command(operation,"product_games.retry_feedback",payload)
    actual = case.store.delivery(case.original.operation_id)
    assert actual.state == "acknowledged" and actual.command == case.original.command
    with case.case.transaction() as database:
        effect = PostgresGameFeedbackStore(database).get(case.original.command.key)
        assert effect.lapse == case.original.command and effect.progress.determined_at == case.original.determined_at
        assert database.execute_native("SELECT count(*) FROM study_game_feedback WHERE card_id=%s",(case.case.card_id,)).fetchone()[0] == 1
        database.execute_native("DELETE FROM product_feedback_retry_requests WHERE workspace_id=%s AND operation_id=%s",(case.workspace,operation))
        database.execute_native("DELETE FROM operation_receipts WHERE operation_id=%s",(operation,))


@pytest.mark.parametrize("change",["boot","mode","source","control"])
def test_native_prepared_handoff_rechecks_actual_boot_mode_source_and_control(native_handoff,monkeypatch,change):
    from app.product_composition import provider_priority as composition
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case = native_handoff
    prepared = composition.prepare_determined_feedback(workspace=case.workspace,connections=case.connections,
        clock=case.clock,admitted=lambda:True,capture=case.capture)
    assert prepared.provider_feedback is not None
    runtime_id = case.runtime_id
    if change == "boot":
        runtime_id = runtime_id[:13] + "9"*32
    elif change == "mode":
        case.modes.record(case.intent(case.workspace,generation=2,phase="inactive"),expected_generation=1)
    elif change == "source":
        changed = case.source.model_copy(update={"quarantined":True})
        with case.case.transaction() as database:
            database.execute_native("UPDATE product_feedback_sources SET payload=%s::jsonb WHERE source_key=%s",
                (changed.model_dump_json(),changed.source_key))
    else:
        from app.games.feedback.ports import JobSpec,PageCommit
        control = delivery_for(case.source.source_key,GameEffectControl(key=case.original.command.key,
            source_revision=1,action="hold",reason="reanalysis"))
        spec = JobSpec(job_id=uuid.uuid4().hex,workspace_id=case.workspace,provider="lichess",
            account="synthetic",window=setup_case().configuration.window,repertoire_ids=(case.case.repertoire_id,))
        case.store.commit_page(PageCommit(job=case.store.enqueue(spec),sources=(),captures=(),deliveries=(control,)))
    with case.case.transaction() as database:
        database.execute_native("SET LOCAL transaction_timeout='250ms'")
        result = composition.apply_prepared_determined_feedback(database,prepared,
            clock=case.clock,current_runtime_id=runtime_id)
        assert not result.progressed
        assert PostgresGameFeedbackStore(database).get(case.original.command.key) is None
    actual = case.store.delivery(case.original.operation_id)
    assert actual.command == case.original.command
    assert actual.state == ("superseded" if change == "control" else "pending")
    assert actual.attempts == 0 and actual.receipt is None


@pytest.fixture
def native_deliveries(owned_postgres):
    from psycopg.types.json import Jsonb
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore,handoff_priority_for
    workspace = "qualification-" + uuid.uuid4().hex
    @contextmanager
    def connections():
        with owned_postgres() as database:
            yield database.raw
    store = PostgresGamesStore(connections)
    def seed(stream, ordinal):
        from app.study.game_feedback import effect_id
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
            "owner_effect_id": effect_id(original.key),
            "qualification": delivery.qualification.model_copy(update={
                "determined_at": f"2026-10-30T00:00:{ordinal:02d}Z"})})
        with connections() as database:
            database.execute("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,1,%s)",
                (identity, Jsonb(case.source.model_dump(mode="json"))))
            database.execute("""INSERT INTO product_feedback_deliveries
                (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload,handoff_priority)
                VALUES(%s,%s,%s,'encounter','pending',0,%s,%s)""",
                (delivery.operation_id, identity, original.key.logical_id(), Jsonb(delivery.model_dump(mode="json")),
                 handoff_priority_for(delivery)))
        return delivery
    return SimpleNamespace(store=store,connections=connections,workspace=workspace,seed=seed)


def test_native_live_and_history_deliveries_have_bounded_fair_progress(native_deliveries):
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    case = native_deliveries
    live = [case.seed("forward", ordinal) for ordinal in (1, 3, 5)]
    history = [case.seed("reconciliation", ordinal) for ordinal in (2, 4)]
    assert case.store.pending_delivery(100,workspace=case.workspace,ready_only=True,
        priority_order=True).operation_id == live[0].operation_id
    seen = []
    # Equal attempt clocks cannot reset the cursor; each fresh store reads the
    # actual persisted service sequence, not an in-process quota counter.
    for _ in range(5):
        restored = PostgresGamesStore(case.connections)
        selected = restored.pending_delivery(100, workspace=case.workspace)
        seen.append(selected.operation_id)
        restored.save_delivery(selected.model_copy(update={"attempts": selected.attempts + 1,
            "next_attempt_at": 200}),serviced=True)
    assert seen == [live[0].operation_id, history[0].operation_id,
        live[1].operation_id, history[1].operation_id, live[2].operation_id]
    with case.connections() as database:
        rows = database.execute("""SELECT payload::text FROM product_feedback_deliveries
            WHERE source_key=ANY(%s)""", ([item.source_key for item in live + history],)).fetchall()
    retained = [Delivery.model_validate_json(row[0]) for row in rows]
    assert sorted(item.service_sequence for item in retained) == [1, 2, 3, 4, 5]
    assert all(item.command == next(original.command for original in live + history
        if original.operation_id == item.operation_id) for item in retained)


@pytest.mark.parametrize("disposition",["ineligible","held"])
def test_native_semantic_qualification_spends_fair_turn_without_transport_attempt(native_deliveries,disposition):
    case = native_deliveries
    live = [case.seed("forward",ordinal) for ordinal in (1,3)]
    history = [case.seed("reconciliation",ordinal) for ordinal in (2,4,5)]
    case.store.save_delivery(live[0].model_copy(update={"attempts":1,"next_attempt_at":200}),serviced=True)
    selected = case.store.pending_delivery(100,workspace=case.workspace)
    assert selected == history[0]
    qualifier = SimpleNamespace(prepare=lambda delivery:delivery.model_copy(update={
        "state":"failed" if disposition == "ineligible" else "pending",
        "qualification_status":disposition,"qualification_reason":"first_departure_covered"
            if disposition == "ineligible" else "first_departure_preparation_unavailable","next_attempt_at":200}))
    coordinator = Coordinator(providers={},normalizer=None,comparer=None,snapshots=None,store=case.store,
        learning=SimpleNamespace(accept_qualified=lambda *args,**kwargs:pytest.fail("ineligible sent toStudy")),
        clock=FakeClock(),qualifier=qualifier)
    coordinator.deliver(selected)
    disposed = case.store.delivery(selected.operation_id)
    assert disposed.attempts == 0 and disposed.service_sequence == 2 and disposed.qualification_status == disposition
    assert case.store.pending_delivery(100,workspace=case.workspace).operation_id == live[1].operation_id


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


def test_native_retryable_determined_hold_recovers_when_due_without_foreground_idle(native_deliveries):
    import time
    case = native_deliveries
    original = case.seed("forward",1)
    held = original.model_copy(update={"qualification_status":"held",
        "qualification_reason":"first_departure_preparation_unavailable","qualification_retryable":True,
        "next_attempt_at":time.time()+60})
    case.store.save_delivery(held)
    assert not case.store.has_determined_delivery(case.workspace)
    due = held.model_copy(update={"next_attempt_at":0})
    case.store.save_delivery(due)
    assert case.store.has_determined_delivery(case.workspace)
    assert case.store.pending_delivery(time.time(),ready_only=True,workspace=case.workspace) == due


def test_native_inventory_distinguishes_qualification_and_acceptance(native_deliveries):
    from tempo_legacy_database.adapters.games.collection_observations import CollectionObservations
    case = native_deliveries
    live = case.seed("forward", 1)
    held = case.seed("reconciliation", 2)
    ineligible = case.seed("reconciliation", 3)
    accepted = case.seed("forward", 4)
    case.store.save_delivery(held.model_copy(update={"qualification_status": "held",
        "qualification_reason": "first_departure_source_changed", "qualification_retryable": False}))
    case.store.save_delivery(ineligible.model_copy(update={"state": "failed", "qualification_status": "ineligible",
        "qualification_reason": "not_selected_first_departure"}))
    receipt = QualifiedReceiver().accept_qualified(accepted.command, qualification=accepted.qualification)
    case.store.save_delivery(accepted.model_copy(update={"state": "acknowledged", "attempts": 1,
        "receipt": receipt}))
    @contextmanager
    def read_connections():
        with case.connections() as database:
            yield database
    read_connections.read_only = True
    report = CollectionObservations(read_connections).delivery(case.workspace)
    assert report["availability"] == "available", report
    assert report["qualification_ready_records"] == report["live_ready_records"] == 1
    assert report["qualification_held_records"] == report["qualification_ineligible_records"] == 1
    assert report["accepted_pending_at_acknowledgment"] == 1
    assert report["study_progress_currently_observed"] is False
    assert report["qualification_grain"] == "retained_delivery_records"
    assert not report["qualification_reasons_truncated"]
    assert {item["diagnostic"] for item in report["qualification_reasons"]} == {
        "first_departure_source_changed", "not_selected_first_departure"}


def test_native_ready_study_head_is_not_blocked_by_later_or_historical_outbox(native_deliveries):
    case = native_deliveries
    historical = case.seed("reconciliation", 1)
    live = case.seed("forward", 3)
    cutoff = dict(before_stream="forward", before_determined_at="2026-10-30T00:00:02Z",
        before_effect_id="game-effect:" + "f" * 64)
    assert not case.store.has_determined_delivery(case.workspace, **cutoff)
    assert case.store.has_determined_delivery(case.workspace, before_stream="reconciliation",
        before_determined_at="2026-10-30T00:00:00Z", before_effect_id="game-effect:" + "0" * 64)
    case.store.save_delivery(live.model_copy(update={"state": "acknowledged", "attempts": 1}))
    assert case.store.has_determined_delivery(case.workspace, before_stream="reconciliation",
        before_determined_at="2026-10-30T00:00:02Z", before_effect_id="game-effect:" + "f" * 64)
    assert not case.store.has_determined_delivery(case.workspace, before_stream="reconciliation",
        before_determined_at="2026-10-30T00:00:00Z", before_effect_id="game-effect:" + "f" * 64)
    assert not case.store.has_determined_delivery(case.workspace, before_stream="reconciliation",
        before_determined_at=historical.determined_at, before_effect_id="game-effect:" + "0" * 64)
    assert case.store.has_determined_delivery(case.workspace, before_stream="reconciliation",
        before_determined_at=historical.determined_at, before_effect_id="game-effect:" + "f" * 64)

