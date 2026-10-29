"""T06 owner policy: frozen clock, explicit FSRS, real shared DTO fixtures."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fsrs import Card
import pytest

from app.contracts.product_v02.learning import GameEffectControl, GameLapse
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.services.scheduling_review_application import apply_scheduling_review
from app.services.scheduling_review_contracts import CardState, MaturityFacts
from app.study.game_feedback import CardFacts, Completion, GameFeedback, effect_id, instant

FIXTURES = Path(__file__).resolve().parents[2] / "contracts/product-v02/fixtures"
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
CARD = "existing-physical-card"


def fixture(name, model):
    return model.model_validate_json(json.dumps(json.loads((FIXTURES / (name + ".json")).read_text())["payload"]))


class Decisions:
    def __init__(self):
        self.mappings = fixture("snapshot", RepertoireSnapshot).decision_presentations

    def current(self, lapse):
        return self.mappings


class MemoryStore:
    def __init__(self):
        self.effects, self.reviews, self.clean_reviews, self.queue = {}, [], [], {}
        self.history = ["retained-original-review"]
        self.facts = CardFacts(1, NOW - timedelta(days=6), True)
        self.scheduling = self
        self.card = CardState("2026-11-20", 20,
            Card(card_id=100, due=NOW + timedelta(days=20), last_review=NOW - timedelta(days=1),
                 stability=20, difficulty=5).to_json(),
            "2026-10-01T12:00:00Z", False, 20, False, "mature", "normal", 0, '["correct"]')
        self.saved = None

    def lock_intake_target(self, card_id): pass

    def lock_target(self, card_id, day): pass
    def lock_effect(self, key): pass
    def get(self, key): return self.effects.get(effect_id(key))
    def save(self, effect, now): self.effects[effect_id(effect.key)] = effect
    def card_facts(self, card_id, day): return self.facts
    def latest_clean_review(self, card_id, revision, after):
        return next((r for r in self.clean_reviews if r.clean and r.revision == revision and r.reviewed_at >= after), None)
    def last_game_review_id(self, key): return len(self.reviews)
    def promote(self, card_id, day): self.queue[(day, card_id)] = "priority-review"
    def owed_page(self, after, limit):
        return tuple(v for k, v in sorted(self.effects.items()) if k > after and v.status in {"pending", "applied"})[:limit]
    def card_effects(self, card_id):
        return tuple(v for v in self.effects.values() if v.card_id == card_id and v.status in {"pending", "applied"})
    def source_review_exists(self, kind, ref):
        return any(r.source_kind == kind and r.source_ref == ref for r in self.reviews)
    def load_card_state(self, card_id, *, require_active): return self.card
    def append_review(self, review): self.reviews.append(review)
    def load_maturity_facts(self, card_id): return MaturityFacts(4, ("again", "correct"), None)
    def save_card_state(self, card_id, state): self.saved = state


@pytest.fixture
def owner():
    store, decisions = MemoryStore(), Decisions()
    clock = [NOW]
    return store, decisions, clock, GameFeedback(store, decisions, clock=lambda: clock[0])


def test_game_lapse_matches_again_transition(owner):
    store, _, _, service = owner
    ordinary = MemoryStore()
    apply_scheduling_review(ordinary, CARD, "again", guided=False, source_kind="study",
        source_ref="attempt:real", light_first_interval_days=7, reviewed_at=NOW, review_day=NOW.date())
    assert service.accept_lapse(fixture("lapse", GameLapse)).status == "applied"
    assert store.saved == ordinary.saved
    assert store.history == ["retained-original-review"]


def test_duplicate_delivery_has_one_effect_and_original_deadline(owner):
    store, _, clock, service = owner
    lapse = fixture("lapse", GameLapse)
    first = service.accept_lapse(lapse)
    clock[0] += timedelta(days=1)
    second = service.accept_lapse(lapse.model_copy(update={"detected_at": clock[0].isoformat()}))
    assert second.outcome == "duplicate"
    assert first.owner_receipt_id == second.owner_receipt_id
    assert instant(second.available_by) == NOW + timedelta(hours=48)
    assert len(store.reviews) == len(store.effects) == 1


def test_recomparison_revision_is_not_new_lapse(owner):
    store, _, _, service = owner
    lapse = fixture("lapse", GameLapse)
    first = service.accept_lapse(lapse)
    recomparison = lapse.model_copy(update={"finding": lapse.finding.model_copy(update={
        "source_revision": 2, "repertoire_revision": 2}), "payload_digest": "c" * 64})
    assert service.accept_lapse(recomparison).owner_receipt_id == first.owner_receipt_id
    assert len(store.reviews) == 1


@pytest.mark.parametrize("invalid", ["unintroduced", "conflicted", "stale", "ambiguous", "unavailable", "unknown_time", "mapping_unavailable", "introduction_old_content"])
def test_unintroduced_conflicted_stale_or_ambiguous_is_rejected(owner, invalid):
    store, decisions, _, service = owner
    lapse = fixture("lapse", GameLapse)
    if invalid == "unintroduced": store.facts = replace(store.facts, introduced_at=None)
    if invalid == "unavailable": store.facts = replace(store.facts, available=False)
    if invalid == "conflicted": decisions.mappings = ()
    if invalid == "ambiguous": decisions.mappings *= 2
    if invalid == "stale": decisions.mappings = (decisions.mappings[0].model_copy(update={"repertoire_revision": 3}),)
    if invalid == "unknown_time": lapse = lapse.model_copy(update={"occurred_at": None})
    if invalid == "mapping_unavailable": decisions.mappings = (decisions.mappings[0].model_copy(update={"available": False}),)
    if invalid == "introduction_old_content": decisions.mappings = (decisions.mappings[0].model_copy(update={"introduction_matches_current_content": False}),)
    assert service.accept_lapse(lapse).status == "held"
    assert not store.reviews and not store.queue


def test_no_fabricated_study_review(owner):
    store, _, _, service = owner
    service.accept_lapse(fixture("lapse", GameLapse))
    assert [(r.rating, r.guided, r.source_kind) for r in store.reviews] == [("again", False, "game_lapse")]
    assert store.reviews[0].source_ref.startswith("game-effect:")


def test_active_attempt_is_preserved(owner):
    store, _, _, service = owner
    store.queue[(NOW.date().isoformat(), "unrelated-active-card")] = "issued-head"
    store.facts = replace(store.facts, active_attempt=True)
    lapse = fixture("lapse", GameLapse)
    assert service.accept_lapse(lapse).status == "pending"
    assert not store.reviews
    store.facts = replace(store.facts, active_attempt=False)
    service.maintain()
    assert service.effect_status(lapse.key).status == "applied"
    assert store.queue[(NOW.date().isoformat(), "unrelated-active-card")] == "issued-head"


def test_pending_feedback_survives_two_days_without_study(owner):
    store, decisions, clock, service = owner
    lapse = fixture("lapse", GameLapse)
    store.facts = replace(store.facts, buried=True)
    assert service.accept_lapse(lapse).reason == "buried"
    clock[0] += timedelta(days=2)
    store.facts = replace(store.facts, buried=False)
    GameFeedback(store, decisions, clock=lambda: clock[0]).maintain()
    assert service.effect_status(lapse.key).status == "applied"
    assert (clock[0].date().isoformat(), CARD) in store.queue


def test_queue_rebuild_and_restart_do_not_erase_obligation(owner):
    store, decisions, clock, service = owner
    lapse = fixture("lapse", GameLapse)
    first = service.accept_lapse(lapse)
    store.queue.clear()
    clock[0] += timedelta(days=3)
    restarted = GameFeedback(store, decisions, clock=lambda: clock[0])
    restarted.maintain()
    assert restarted.effect_status(lapse.key).available_by == first.available_by
    assert len(store.reviews) == 1 and (clock[0].date().isoformat(), CARD) in store.queue


def test_genuine_review_settles_once(owner):
    store, _, clock, service = owner
    lapse = fixture("lapse", GameLapse)
    service.accept_lapse(lapse)
    completion = Completion(100, CARD, 1, NOW + timedelta(hours=1), True, "study")
    for unsuitable in [replace(completion, clean=False), replace(completion, source_kind="game_lapse")]:
        service.settle(unsuitable)
        assert service.effect_status(lapse.key).status == "applied"
    clock[0] = completion.reviewed_at
    service.settle(completion)
    service.settle(completion)
    assert service.effect_status(lapse.key).status == "settled"
    assert store.get(lapse.key).settlement_review_id == 100 and len(store.reviews) == 1


def test_late_evidence_does_not_rewind_state(owner):
    store, _, _, service = owner
    before = store.card
    store.clean_reviews.append(Completion(7, CARD, 1, NOW - timedelta(hours=1), True, "study"))
    assert service.accept_lapse(fixture("lapse", GameLapse)).status == "settled"
    assert store.card == before and store.saved is None and not store.reviews


def test_changed_source_is_quarantined(owner):
    store, _, _, service = owner
    lapse = fixture("lapse", GameLapse)
    store.facts = replace(store.facts, active_attempt=True)
    first = service.accept_lapse(lapse)
    changed = lapse.model_copy(update={"finding": lapse.finding.model_copy(update={"source_digest": "d" * 64})})
    assert service.accept_lapse(changed).outcome == "conflict"
    store.facts = replace(store.facts, active_attempt=False)
    service.maintain()
    assert service.accept_lapse(lapse).status == "held"
    assert service.effect_status(lapse.key).available_by == first.available_by and not store.reviews
    # The required withdrawal regression includes the delayed original command.
    other = MemoryStore()
    pending = GameFeedback(other, Decisions(), clock=lambda: NOW)
    control = fixture("hold", GameEffectControl)
    assert control.withdrawal is None  # No producer-guessed owner obligation identity.
    assert pending.control_effect(control).status == "held"
    assert pending.accept_lapse(lapse).status == "held"
    pending.maintain()
    assert not other.reviews and len(other.effects) == 1


def test_withdraw_after_apply_preserves_history(owner):
    store, _, _, service = owner
    lapse = fixture("lapse", GameLapse)
    applied = service.accept_lapse(lapse)
    result = service.control_effect(fixture("withdraw", GameEffectControl))
    assert result.outcome == "historical_preserved" and result.applied_at == applied.applied_at
    assert len(store.reviews) == 1 and store.saved is not None


def test_current_owner_provider_alias_preserves_capture_and_rejects_unknowns():
    from types import SimpleNamespace
    from tempo_game_evidence_contracts.legacy import fingerprint
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions, CurrentDecisionUnavailable

    # The unchanged native cases own capture validation and all later row locks.
    # Stop at the next owner fence to isolate provider scope matching here.
    class BusyOwner:
        read_only = False
        raw = SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name='INTRANS')))
        def __init__(self): self.calls = 0
        def execute_native(self, *args):
            self.calls += 1
            return SimpleNamespace(fetchone=lambda: (False,))

    original = fixture('lapse', GameLapse)
    for retained, command, permitted in [
        ('chess.com','chesscom',True), ('chesscom','chesscom',True),
        ('lichess','lichess',True), ('unknown','chesscom',False),
        ('Chess.com','chesscom',False), ('chess-com','chesscom',False),
        ('chess.com','lichess',False),
    ]:
        lapse = original.model_copy(update={
            'key':original.key.model_copy(update={'provider':command}),
            'finding':original.finding.model_copy(update={'provider':command})})
        game = dict(id=lapse.key.game_id, provider=retained, played_at=lapse.occurred_at,
                    moves_json=json.dumps([lapse.finding.played_uci]))
        source = dict(game=game, export_digest=fingerprint(game))
        before = json.dumps(source, sort_keys=True)
        authority = object.__new__(CurrentOwnerDecisions)
        authority.database = BusyOwner()
        authority.source = source
        authority.authority = SimpleNamespace(workspace_id=lapse.key.workspace_id,
                                              repertoire_id=lapse.finding.repertoire_id)
        authority.proof = SimpleNamespace(prefix_policies=[[lapse.finding.repertoire_id]],
            scope=SimpleNamespace(decision_plies=[0], game_id=lapse.key.game_id,
                                  occurred_at=lapse.occurred_at, moves=[lapse.finding.played_uci]))
        reason = 'current_owner_graph_busy' if permitted else 'retained_owner_command_scope_mismatch'
        with pytest.raises(CurrentDecisionUnavailable, match=reason):
            authority.current(lapse)
        assert authority.database.calls == int(permitted)
        assert json.dumps(source, sort_keys=True) == before
        assert source['game']['provider'] == retained
        assert source['export_digest'] == fingerprint(source['game'])


@pytest.fixture
def encounter_owner():
    from types import SimpleNamespace
    from app.contracts.product_v02.learning import GameEncounter
    from app.study.encounter_feedback import EncounterFeedback, EncounterIntroductionPolicy

    command = fixture('encounters/unintroduced-missed', GameEncounter)

    class Store(MemoryStore):
        def encounter_card_facts(self, card_id, day): return self.facts
        def promote_encounter(self, card_id, day): self.queue[(day, card_id)] = 'encounter-priority'

    class Current:
        mappings = (command.decision,)
        def current_encounter(self, command): return self.mappings

    class Introductions:
        reason = None
        charges = 0
        def admit_encounter(self, day, *, repertoire_id, decision_id, target):
            assert target.decision_id == decision_id and target.repertoire_id == repertoire_id
            if self.reason:
                return SimpleNamespace(status='deferred', reason=self.reason)
            if store.facts.introduced_at is None:
                self.charges += 1
                store.facts = replace(store.facts, introduced_at=clock[0])
            return SimpleNamespace(status='introduced', reason=None)

    store, decisions, clock, introductions = Store(), Current(), [NOW], Introductions()
    store.facts = replace(store.facts, introduced_at=None)
    service = EncounterFeedback(store, decisions, clock=lambda:clock[0], introductions=introductions,
        introduction_policy=EncounterIntroductionPolicy('encounter_exception'))
    return store, decisions, clock, introductions, service, command


def accept_and_refresh(service,command):
    """Keep refresh-policy cases explicit; intake/failure isolation has native guards."""
    accepted=service.accept_encounter(command)
    if accepted.status=='pending' and accepted.outcome=='accepted':
        return service.refresh_encounter(command.key)
    return accepted


@pytest.mark.parametrize('observation', ['matched', 'missed'])
def test_encounter_first_introduction_once_without_review_or_again(encounter_owner, observation):
    store, _, clock, introductions, service, command = encounter_owner
    command = command.model_copy(update=dict(observation=observation,
        played_uci=command.expected_uci if observation=='matched' else command.played_uci))
    before = store.card
    receipt = accept_and_refresh(service,command)
    clock[0] += timedelta(days=1)
    duplicate = accept_and_refresh(service,command.model_copy(update={'detected_at':clock[0].isoformat()}))
    assert receipt.disposition == 'first_introduction' and receipt.admission_status == 'available'
    assert receipt.observation_recorded and receipt.observation == observation
    assert duplicate.owner_receipt_id == receipt.owner_receipt_id and duplicate.detected_at == command.detected_at
    assert introductions.charges == 1 and store.facts.introduced_at == NOW
    assert store.saved is None and store.card == before and store.reviews == []
    assert store.history == ['retained-original-review']


def test_encounter_later_recomparison_cannot_become_lapse(encounter_owner):
    from app.contracts.product_v02.learning import GameEncounter
    store, _, _, introductions, service, command = encounter_owner
    first = accept_and_refresh(service,command)
    lapse = fixture('lapse', GameLapse).model_dump(mode='json')
    lapse.update(key=command.key.model_dump(mode='json'),detected_at=command.detected_at,occurred_at=command.occurred_at)
    lapse['finding'].update(provider=command.key.provider,game_id=command.key.game_id,
        source_revision=command.source.source_revision,source_digest=command.source.source_digest,
        decision_id=command.key.decision_id,position_key=command.decision.position_key,
        expected_uci=[command.expected_uci],played_uci=command.played_uci,
        repertoire_id=command.decision.repertoire_id,repertoire_revision=command.decision.repertoire_revision,
        presentation_id=command.decision.presentation_id,presentation_revision=command.decision.presentation_revision)
    duplicate = service.accept_lapse(GameLapse.model_validate_json(json.dumps(lapse)))
    assert duplicate.owner_receipt_id == first.owner_receipt_id and duplicate.outcome == 'duplicate'
    assert store.reviews == [] and store.saved is None and introductions.charges == 1
    assert store.get(command.key).encounter == GameEncounter.model_validate_json(command.model_dump_json())


def test_historical_missed_encounter_retains_priority_after_later_learning(encounter_owner):
    store, _, _, introductions, service, command = encounter_owner
    store.facts = replace(store.facts, introduced_at=NOW-timedelta(hours=2))
    store.clean_reviews.append(Completion(77,CARD,1,NOW-timedelta(hours=1),True,'study'))
    before = store.card
    receipt = accept_and_refresh(service,command)
    assert receipt.disposition == 'review_priority' and receipt.admission_status == 'available'
    assert receipt.observation_recorded and receipt.observation == 'missed'
    assert (NOW.date().isoformat(),CARD) in store.queue
    assert store.saved is None and store.card == before and not store.reviews and introductions.charges == 0


def test_correct_introduced_encounter_is_frequency_without_failure(encounter_owner):
    store, _, _, introductions, service, command = encounter_owner
    store.facts = replace(store.facts, introduced_at=NOW-timedelta(days=1))
    receipt = accept_and_refresh(service,command.model_copy(update=dict(observation='matched',played_uci=command.expected_uci)))
    assert receipt.disposition == 'frequency_only' and receipt.admission_status == 'not_requested'
    assert receipt.observation_recorded and receipt.available_by is None
    assert not store.reviews and not store.queue and store.saved is None and introductions.charges == 0
    service.control_effect(GameEffectControl(key=command.key,source_revision=2,action='hold',reason='source_excluded'))
    assert service.encounter_status(command.key).admission_status == 'not_requested'


def test_ordinary_introduction_racing_pending_encounter_preserves_schedule_and_original_intent(encounter_owner):
    store, _, clock, introductions, service, command = encounter_owner
    introductions.reason = 'paused'
    first = accept_and_refresh(service,command)
    assert first.admission_status == 'pending' and first.available_by is None
    clock[0] += timedelta(days=1)
    store.facts = replace(store.facts, introduced_at=clock[0]-timedelta(hours=1))
    before = store.card
    service.maintain()
    current = service.encounter_status(command.key)
    assert current.disposition == first.disposition == 'first_introduction'
    assert current.admission_status == 'available' and current.detected_at == first.detected_at
    assert introductions.charges == 0 and store.card == before and store.saved is None and not store.reviews


@pytest.mark.parametrize('reason', ['disabled','paused'])
def test_encounter_explicit_controls_remain_pending_without_deadline(encounter_owner, reason):
    store, _, _, introductions, service, command = encounter_owner
    introductions.reason = reason
    receipt = accept_and_refresh(service,command)
    assert receipt.admission_status == 'pending' and receipt.deferred_reason == reason
    assert receipt.available_by is None and receipt.detected_at == command.detected_at
    assert receipt.observation_recorded and introductions.charges == 0 and not store.reviews and not store.queue


def test_encounter_hold_before_delivery_fences_both_command_kinds(encounter_owner):
    store, _, _, introductions, service, command = encounter_owner
    service.control_effect(GameEffectControl(key=command.key,source_revision=1,action='hold',reason='reanalysis'))
    receipt = accept_and_refresh(service,command)
    assert receipt.status == 'held' and not receipt.observation_recorded
    service.maintain()
    assert not store.queue and not store.reviews and introductions.charges == 0


def test_encounter_active_attempt_and_unknown_policy_do_not_mutate(encounter_owner):
    from app.study.encounter_feedback import EncounterIntroductionPolicy
    store, _, _, introductions, service, command = encounter_owner
    store.facts = replace(store.facts, active_attempt=True)
    assert accept_and_refresh(service,command).deferred_reason == 'active_attempt'
    store.facts = replace(store.facts, active_attempt=False)
    service.introduction_policy = None
    service.maintain()
    assert service.encounter_status(command.key).reason == 'encounter_introduction_policy_pending'
    service.introduction_policy = EncounterIntroductionPolicy('encounter_exception')
    introductions.reason = 'paused'
    service.maintain()
    assert service.encounter_status(command.key).deferred_reason == 'paused'
    assert introductions.charges == 0 and not store.reviews and not store.queue


def test_encounter_targeted_exception_delegates_exact_target_without_again(encounter_owner):
    from app.study.encounter_feedback import EncounterIntroductionPolicy
    store,_,_,introductions,service,command=encounter_owner
    service.introduction_policy=EncounterIntroductionPolicy('encounter_exception')
    receipt=accept_and_refresh(service,command)
    assert receipt.disposition=='first_introduction' and receipt.admission_status=='available'
    assert introductions.charges==1 and not store.reviews and store.saved is None


def test_encounter_old_allowance_capability_is_configuration_pending(encounter_owner):
    store,_,_,introductions,service,command=encounter_owner
    introductions.reason='study_only'
    receipt=accept_and_refresh(service,command)
    assert receipt.reason=='encounter_exception_capability_required' and receipt.deferred_reason is None
    assert receipt.admission_status=='pending' and receipt.available_by is None
    assert introductions.charges==0 and not store.reviews


def test_encounter_status_reuses_existing_lapse_ledger_without_another_effect(owner):
    from app.study.encounter_feedback import EncounterFeedback
    store,decisions,clock,lapse_receiver=owner
    command=fixture('lapse',GameLapse)
    first=lapse_receiver.accept_lapse(command)
    encounters=EncounterFeedback(store,decisions,clock=lambda:clock[0],introductions=None,introduction_policy=None)
    status=encounters.encounter_status(command.key)
    assert status.owner_receipt_id==first.owner_receipt_id and status.disposition=='existing_lapse'
    assert status.observation=='missed' and status.observation_recorded
    assert len(store.effects)==len(store.reviews)==1
