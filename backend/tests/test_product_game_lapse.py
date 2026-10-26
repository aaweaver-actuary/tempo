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


@pytest.mark.parametrize("invalid", ["unintroduced", "conflicted", "stale", "ambiguous", "unavailable", "unknown_time"])
def test_unintroduced_conflicted_stale_or_ambiguous_is_rejected(owner, invalid):
    store, decisions, _, service = owner
    lapse = fixture("lapse", GameLapse)
    if invalid == "unintroduced": store.facts = replace(store.facts, introduced_at=None)
    if invalid == "unavailable": store.facts = replace(store.facts, available=False)
    if invalid == "conflicted": decisions.mappings = ()
    if invalid == "ambiguous": decisions.mappings *= 2
    if invalid == "stale": decisions.mappings = (decisions.mappings[0].model_copy(update={"repertoire_revision": 3}),)
    if invalid == "unknown_time": lapse = lapse.model_copy(update={"occurred_at": None})
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
    assert pending.control_effect(fixture("hold", GameEffectControl)).status == "held"
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
