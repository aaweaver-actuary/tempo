"""T14 pure selection + fake semantic owner/store; no provider/DB/runtime."""
from dataclasses import replace
from pathlib import Path

import pytest

from app.contracts.product_v02.analysis import RankedDecision, RankingCandidate, RankingInput, RankingOutput
from app.study.introductions.application import Introductions
from app.study.introductions.policy import Candidate, RankingContext, RequiredWork, select
from app.study.introductions.ports import AllocationState, OwnerFacts

DAY = "2026-10-26"
NEXT_DAY = "2026-10-27"
CONTEXT = RankingContext((("a", 1, "source-a"), ("b", 1, "source-b")), "explicit-settings-1")


def candidate(identity, repertoire="a", **changes):
    return replace(Candidate(identity, identity, repertoire, "definition-1", 0,
                             approved=True, legal=True, conflicted=False, mapped=True, playable_context=True), **changes)


def ranking(*identities):
    return RankingOutput(based_on=RankingInput(
        repertoire_revisions=tuple((name, rev) for name, rev, _ in CONTEXT.repertoires),
        candidates=tuple(RankingCandidate(decision_id=identity, repertoire_id=repertoire,
                         reach_probability=probability, evidence_support=None,
                         uncertainty=None, personal_encounters=None)
                         for identity, repertoire, probability in identities), evidence_ids=("synthetic-cohort",)),
        ordered=tuple(RankedDecision(decision_id=identity, rank=index + 1, reason="supplied_reach")
                      for index, (identity, _, _) in enumerate(identities)), unavailable_reason=None)


def choose(rows, **kwargs):
    defaults = dict(candidates=tuple(rows), ranking=None, global_budget=10,
                    weights=(("a", 1), ("b", 1)), enabled=True)
    return select(**(defaults | kwargs))


class MemoryStore:
    def __init__(self):
        self.cursor = None
        self.periods = {}
        self.receipts = {}
        self.rankings = {}

    def state(self, day):
        return AllocationState(self.cursor, day in self.periods, self.periods.get(day),
                               tuple(r for r in self.receipts.values() if r.day == day))
    def saved_ranking(self, context): return self.rankings.get(context)
    def save_ranking(self, context, value): self.rankings[context] = value
    def lock_allocation(self): pass
    def receipt(self, presentation): return self.receipts.get(presentation)
    def record_introduction(self, receipt, *, period_rotation_after):
        assert receipt.presentation_id not in self.receipts
        self.periods.setdefault(receipt.day, period_rotation_after)
        self.receipts[receipt.presentation_id] = receipt
        self.cursor = receipt.repertoire_id


class Owner:
    def __init__(self, rows, **changes):
        self.value = OwnerFacts(CONTEXT, tuple(rows), 1, (("a", 1), ("b", 1)), enabled=True)
        self.value = replace(self.value, **changes)
        self.effects = []
        self.active_attempt = object()

    def facts(self, day): return self.value
    def introduce(self, day, choice):
        self.effects.append((day, choice))
        self.value = replace(self.value, introduced=self.value.introduced | {choice.presentation_id})
        return "introduced"


def test_twenty_percent_before_two_percent_with_equal_weights():
    low, high = candidate("two", "a"), candidate("twenty", "b")
    plan = choose((low, high), global_budget=2, ranking=ranking(("twenty", "b", .2), ("two", "a", .02)))
    assert plan.introductions == (high, low)


def test_global_budget_not_multiplied_by_repertoire():
    assert len(choose([candidate(str(n), "a" if n % 2 else "b") for n in range(10)], global_budget=3).introductions) == 3
    assert len(choose([candidate("x"), candidate("y", "b")], global_budget=2,
                      spent=(("earlier", "a"),)).introductions) == 1


def test_weights_and_empty_repertoire_spillover():
    rows = [candidate(f"a{n}") for n in range(6)] + [candidate(f"b{n}", "b") for n in range(6)]
    result = choose(rows, global_budget=4, weights=(("a", 3), ("b", 1)))
    assert [item.repertoire_id for item in result.introductions].count("a") == 3
    assert len(choose(rows[:6], global_budget=4).introductions) == 4
    assert not choose(rows, weights=(("a", 0), ("b", 0))).introductions


def test_multiday_one_slot_equal_weights_both_served():
    store = MemoryStore()
    owner = Owner([candidate("a1"), candidate("a2"), candidate("b1", "b"), candidate("b2", "b")])
    for day, expected in ((DAY, "a"), (NEXT_DAY, "b")):
        service = Introductions(store, owner)  # explicit reload
        before = store.state(day)
        plans = [service.preview(day) for _ in range(4)]
        assert plans == [plans[0]] * 4 and store.state(day) == before
        selected = plans[0].introductions[0]
        assert selected.repertoire_id == expected
        assert service.commit(day, selected, CONTEXT).status == "introduced"
        committed = store.state(day)
        for _ in range(3):
            assert Introductions(store, owner).commit(day, selected, CONTEXT).status == "replayed"
            assert not service.preview(day).introductions
            assert store.state(day) == committed
    assert len(store.receipts) == len(owner.effects) == 2


def test_rebuild_does_not_advance_fairness_twice():
    store, owner = MemoryStore(), Owner([candidate("a"), candidate("b", "b")])
    service = Introductions(store, owner)
    for _ in range(10): assert service.preview(DAY).introductions[0].repertoire_id == "a"
    assert store.cursor is None and not store.periods
    chosen = service.preview(DAY).introductions[0]
    service.commit(DAY, chosen, CONTEXT)
    for _ in range(10): service.preview(NEXT_DAY)
    assert store.cursor == "a" and NEXT_DAY not in store.periods


def test_shared_card_and_transposition_charge_once():
    shared = candidate("decision-a", presentation_id="physical-shared")
    transposed = candidate("decision-b", "b", presentation_id="physical-shared")
    plan = choose([shared, transposed, candidate("other", "b")], global_budget=4)
    assert len(plan.introductions) == 2
    assert plan.introductions[0].repertoire_id == "a"  # selected membership receives credit
    # Equal position alone would not dedupe independently supplied identities.
    assert len(choose([candidate("independent-a"), candidate("independent-b")]).introductions) == 2


def test_due_reviews_and_game_obligations_precede_new_cards():
    mandatory = (RequiredWork("due", "due"), RequiredWork("lapse", "obligation"), RequiredWork("due", "due"))
    assert choose([candidate("new")], mandatory=mandatory).ordered_presentations == ("due", "lapse", "new")
    assert choose([candidate("new")], global_budget=0, mandatory=mandatory).ordered_presentations == ("due", "lapse")


def test_rebuild_and_retry_do_not_recharge():
    store, owner = MemoryStore(), Owner([candidate("one")])
    service = Introductions(store, owner)
    choice = service.preview(DAY).introductions[0]
    assert service.commit(DAY, choice, CONTEXT).status == "introduced"
    assert service.commit(NEXT_DAY, choice, CONTEXT).status == "replayed"
    assert len(owner.effects) == len(store.receipts) == 1


@pytest.mark.parametrize("outcome", ["stale", "already_introduced"])
def test_owner_effect_recheck_does_not_charge_rejected_or_existing_material(outcome):
    class RecheckingOwner(Owner):
        def introduce(self, day, choice): return outcome
    store, owner = MemoryStore(), RecheckingOwner([candidate("one")])
    service = Introductions(store, owner)
    choice = service.preview(DAY).introductions[0]
    assert service.commit(DAY, choice, CONTEXT).status == outcome
    assert not store.receipts and not store.periods and store.cursor is None


@pytest.mark.parametrize("facts", [dict(conflicted=True), dict(approved=False), dict(mapped=False),
                                  dict(legal=False), dict(playable_context=False), dict(definition_revision="")])
def test_conflicted_unapproved_or_unmapped_candidate_excluded(facts):
    assert not choose([candidate("unsafe", **facts)]).introductions


def test_useful_deep_card_not_blocked_by_mature_ancestor_gate():
    # Current owner proves the full legal route. No ancestor schedule is needed.
    deep = candidate("approved-deep-ply-40")
    assert choose([deep]).introductions == (deep,)


def test_offline_uses_compatible_saved_or_fallback_order():
    first, second = candidate("fallback", fallback_order=0), candidate("ranked", fallback_order=1)
    store, owner = MemoryStore(), Owner([first, second])
    service = Introductions(store, owner)
    assert service.preview(DAY).introductions == (first,)
    value = ranking(("ranked", "a", .2), ("fallback", "a", .02))
    assert service.accept_ranking(DAY, CONTEXT, value) == "accepted"
    unavailable = value.model_copy(update={"unavailable_reason": "offline"})
    assert service.accept_ranking(DAY, CONTEXT, unavailable) == "rejected"
    assert service.preview(DAY).introductions == (second,)
    active = owner.active_attempt
    assert service.accept_ranking(DAY, CONTEXT, value) == "accepted"
    assert owner.active_attempt is active and not owner.effects


def test_settings_revision_invalidates_incompatible_ranking():
    store, owner = MemoryStore(), Owner([candidate("fallback"), candidate("ranked", fallback_order=1)])
    service = Introductions(store, owner)
    value = ranking(("ranked", "a", .2))
    service.accept_ranking(DAY, CONTEXT, value)
    owner.value = replace(owner.value, context=replace(CONTEXT, settings_revision="explicit-settings-2"))
    assert service.preview(DAY).introductions[0].decision_id == "fallback"
    assert service.accept_ranking(DAY, CONTEXT, value) == "rejected"
    assert service.commit(DAY, candidate("fallback"), CONTEXT).status == "stale"
    owner.value = replace(owner.value, context=replace(CONTEXT, repertoires=(("a", 1, "changed-digest"), ("b", 1, "source-b"))))
    assert service.preview(DAY).introductions[0].decision_id == "fallback"


@pytest.mark.parametrize("control", [dict(study_only=True), dict(paused=True), dict(enabled=False)])
def test_study_only_control_preserved(control):
    assert not choose([candidate("new")], **control).introductions


def test_joined_card_costs_zero():
    joined = candidate("already-joined", introduced=True)
    assert choose([joined], mandatory=(RequiredWork(joined.presentation_id, "due"),), global_budget=0).ordered_presentations == ("already-joined",)
    assert not choose([joined]).introductions


@pytest.mark.parametrize("weights", [(("a", -1),), (("a", float("nan")),), (("a", float("inf")),),
                                     (("a", True),), (("a", 1), ("a", 2))])
def test_invalid_weights_rejected(weights):
    with pytest.raises(ValueError): choose([candidate("new")], weights=weights)


def test_daily_quota_stays_fixed_after_each_commit():
    owner = Owner([candidate(f"a{n}") for n in range(6)] + [candidate(f"b{n}", "b") for n in range(6)],
                  global_budget=4, weights=(("a", 3), ("b", 1)))
    store, selected = MemoryStore(), []
    for _ in range(4):
        service = Introductions(store, owner)
        choice = service.preview(DAY).introductions[0]
        selected.append(choice.repertoire_id)
        assert service.commit(DAY, choice, CONTEXT).status == "introduced"
    assert selected.count("a") == 3 and selected.count("b") == 1
    assert store.periods == {DAY: None}


def test_ranking_rejects_unknown_ids_duplicates_and_stale_source_revision():
    service = Introductions(MemoryStore(), Owner([candidate("one")]))
    valid = ranking(("one", "a", .2))
    bad_id = valid.model_copy(update={"ordered": (RankedDecision(decision_id="unknown", rank=1, reason="bad"),)})
    duplicate = valid.model_copy(update={"ordered": valid.ordered * 2})
    stale = valid.model_copy(update={"based_on": valid.based_on.model_copy(update={"repertoire_revisions": (("a", 2), ("b", 1))})})
    for value in (bad_id, duplicate, stale):
        assert service.accept_ranking(DAY, CONTEXT, value) == "rejected"


def test_new_product_modules_reuse_scoped_import_boundary():
    from test_product_contracts import boundary_errors, has_import_cycle, import_targets
    root = Path(__file__).resolve().parents[2]
    sources = {"app.study.introductions." + path.stem: path.read_text()
               for path in (root / "backend/app/study/introductions").glob("*.py")}
    assert boundary_errors([("study", source, module) for module, source in sources.items()]) == []
    assert not any(name == "app.analysis" or name.startswith("app.analysis.")
                   for module, source in sources.items() for name in import_targets(source, module))
    assert not has_import_cycle(sources)
