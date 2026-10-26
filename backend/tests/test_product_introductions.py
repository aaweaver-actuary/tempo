"""T14 pure selection + fake semantic owner/store; no provider/DB/runtime."""
from dataclasses import replace
from pathlib import Path

import pytest

from app.contracts.product_v02.analysis import RankedDecision, RankingCandidate, RankingInput, RankingOutput
from app.study.introductions.application import Introductions
from app.study.introductions.policy import Candidate, RankingContext, RequiredWork, select
from app.study.introductions.ports import AllocationState, OwnerFacts


@pytest.mark.parametrize('study_only,global_limit,repertoire_limit,credits,reason', [
    (True, 10, None, (), 'study_only'),
    (False, 0, None, (), 'zero_limit'),
    (False, 10, 0, (), 'zero_limit'),
    (False, 1, None, ('b',), 'introduction_allowance'),
    (False, 10, 1, ('a',), 'introduction_allowance'),
    (False, 2, None, ('b',), None),
    (False, True, None, (), 'current_controls_unproved'),
])
def test_encounter_ordinary_saved_allowance(study_only,global_limit,repertoire_limit,credits,reason):
    from app.study.introductions.application import ordinary_encounter_allowance
    assert ordinary_encounter_allowance(study_only=study_only,global_limit=global_limit,
        repertoire_limit=repertoire_limit,introduction_count=len(credits),repertoire_count=credits.count('a')) == reason


def test_encounter_target_retains_complete_alternatives_not_unrelated_routes():
    import chess
    from app.contracts.product_v02.repertoire import ImmutableAuthoredRoute
    from app.study.introductions.application import target_alternatives
    from tempo_evidence_math import opening_position_key
    board = chess.Board()
    board.push_uci('e2e4'); board.push_uci('e7e5')
    expected = ((opening_position_key(board.fen()),'g1f3'),)
    def route(identity,moves,start=chess.STARTING_FEN):
        return ImmutableAuthoredRoute(repertoire_id='a',route_id=identity,policy_id='a',
            learner_color='white',starting_fen=start,moves=moves)
    target = route('target',('e2e4','e7e5','g1f3'))
    unrelated = tuple(route(str(n),('d2d4','d7d5','c2c4')) for n in range(80))
    assert target_alternatives((*unrelated,target),expected=expected,learner_color='white').routes == (target,)
    conflict = route('unmapped-conflict',('f1c4',),board.fen())
    assert target_alternatives((*unrelated,target,conflict),expected=expected,
        learner_color='white').reason == 'target_response_conflict'
    relevant = tuple(route(str(n),target.moves) for n in range(65))
    assert target_alternatives(relevant,expected=expected,
        learner_color='white').reason == 'target_alternatives_bound'
    assert target_alternatives((target,),expected=(),learner_color='white').reason == 'target_context_unproved'


def test_encounter_target_checks_transposed_and_later_physical_decisions():
    import chess
    from app.contracts.product_v02.repertoire import ImmutableAuthoredRoute
    from app.study.introductions.application import target_alternatives
    from tempo_evidence_math import opening_position_key
    def route(identity,moves):
        return ImmutableAuthoredRoute(repertoire_id='a',route_id=identity,policy_id='a',
            learner_color='white',starting_fen=chess.STARTING_FEN,moves=moves)
    board = chess.Board()
    for uci in ('g1f3','g8f6','g2g3','g7g6'): board.push_uci(uci)
    target = route('target',('g1f3','g8f6','g2g3','g7g6','f1g2'))
    other = route('transposition',('g2g3','g7g6','g1f3','g8f6','d2d4'))
    assert target_alternatives((target,other),
        expected=((opening_position_key(board.fen()),'f1g2'),),
        learner_color='white').reason == 'target_response_conflict'

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


@pytest.mark.parametrize('control',[dict(enabled='false'),dict(paused=1)])
def test_current_owner_settings_require_explicit_boolean_controls(control):
    from app.study.introductions.ports import IntroductionSettings
    with pytest.raises(ValueError):
        IntroductionSettings('explicit',1,(("a",1),),**control)


def test_conflict_admission_delegates_exact_scoped_current_revision():
    from app.study.introductions.application import introduction_is_held
    class Conflicts:
        def __init__(self): self.calls = []
        def blocked(self, repertoire_id, card_id, revision):
            self.calls.append((repertoire_id,card_id,revision))
            return (repertoire_id,card_id,revision) == ('held','shared',3)
    conflicts = Conflicts()
    assert introduction_is_held(conflicts,repertoire_id='held',presentation_id='shared',presentation_revision=3)
    assert not introduction_is_held(conflicts,repertoire_id='other',presentation_id='shared',presentation_revision=3)
    assert not introduction_is_held(conflicts,repertoire_id='held',presentation_id='shared',presentation_revision=4)
    assert conflicts.calls == [('held','shared',3),('other','shared',3),('held','shared',4)]


def test_conflict_admission_absent_capability_retains_existing_behavior():
    from app.study.introductions.application import introduction_is_held
    assert not introduction_is_held(None,repertoire_id='rep',presentation_id='card',presentation_revision=1)


@pytest.mark.parametrize('answer',[True,None,'false',0])
def test_conflict_admission_requires_explicit_release(answer):
    from app.study.introductions.application import introduction_is_held
    class Conflicts:
        def blocked(self,*args): return answer
    assert introduction_is_held(Conflicts(),repertoire_id='rep',presentation_id='card',presentation_revision=1)


def test_conflict_admission_unknown_physical_revision_is_not_a_release():
    from app.study.introductions.application import introduction_is_held
    class Conflicts:
        def blocked(self,*args): raise AssertionError('unknown identity must not be queried')
    assert introduction_is_held(Conflicts(),repertoire_id='rep',presentation_id='card',presentation_revision=0)


def repertoire_only_snapshot():
    import json
    from app.contracts.product_v02.repertoire import RepertoireSnapshot
    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / 'contracts/product-v02/fixtures/snapshot.json').read_text())['payload']
    payload['membership']['source_revision'] = payload['membership']['digest']
    payload['membership']['scope_id'] = 'repertoire:white'
    return RepertoireSnapshot.model_validate_json(json.dumps(payload))


def test_repertoire_only_source_needs_no_game_capture_authority():
    from app.study.introductions.ports import RepertoireIntroductionSource
    source = RepertoireIntroductionSource(repertoire_only_snapshot())
    assert source.repertoire_id == 'white'
    assert not hasattr(source,'authority')


def test_repertoire_only_binding_ignores_capture_and_introduction_chronology():
    from app.study.introductions.application import repertoire_introduction_binding
    source = repertoire_only_snapshot()
    changed = source.model_copy(update={
        'membership':source.membership.model_copy(update={'snapshot_id':'new-capture','digest':'d'*64,
            'source_revision':'d'*64,'captured_at':'2026-10-27T12:00:00Z'}),
        'decision_presentations':(source.decision_presentations[0].model_copy(update={
            'introduction':'unintroduced','introduced_at':None,'introduction_matches_current_content':False}),)})
    assert repertoire_introduction_binding(source) == repertoire_introduction_binding(changed)


@pytest.mark.parametrize('change',['route','physical_revision','source_revision'])
def test_repertoire_only_binding_retains_exact_content_and_source_revision(change):
    from app.study.introductions.application import repertoire_introduction_binding
    source = repertoire_only_snapshot()
    if change == 'route':
        changed = source.model_copy(update={'membership':source.membership.model_copy(update={
            'routes':(source.membership.routes[0].model_copy(update={'moves':('d2d4',)}),)})})
    elif change == 'physical_revision':
        changed = source.model_copy(update={'decision_presentations':(
            source.decision_presentations[0].model_copy(update={'presentation_revision':2}),)})
    else:
        changed = source.model_copy(update={'membership':source.membership.model_copy(update={'revision':2}),
            'decision_presentations':(source.decision_presentations[0].model_copy(update={'repertoire_revision':2}),)})
    assert repertoire_introduction_binding(source) != repertoire_introduction_binding(changed)


@pytest.mark.parametrize('change',[dict(complete=False),dict(available=False,unavailable_reason='unavailable')])
def test_repertoire_only_source_rejects_unproved_current_snapshot(change):
    from app.study.introductions.ports import RepertoireIntroductionSource
    source = repertoire_only_snapshot()
    with pytest.raises(ValueError):
        RepertoireIntroductionSource(source.model_copy(update={'membership':source.membership.model_copy(update=change)}))


def test_encounter_priority_precedes_saved_rank_without_requiring_ranking():
    encountered, other = candidate('encounter',fallback_order=1), candidate('other')
    for saved in (None,ranking(('other','a',.2),('encounter','a',.02))):
        plan = choose((other,encountered),global_budget=1,weights=(('a',1),),ranking=saved,
                      priority=('a','encounter'))
        assert plan.introductions == (encountered,)


@pytest.mark.parametrize('control',[dict(global_budget=0),dict(paused=True),dict(study_only=True),
                                   dict(enabled=False),dict(weights=(('a',0),))])
def test_encounter_priority_cannot_override_budget_weight_or_saved_pause(control):
    assert not choose((candidate('encounter'),),priority=('a','encounter'),**control).introductions


def test_encounter_priority_retains_required_work_and_owed_repertoire_allocation():
    a,b = candidate('a'),candidate('b','b')
    required = (RequiredWork('due','due'),RequiredWork('obligation','obligation'))
    plan = choose((a,b),global_budget=2,priority=('b','b'),mandatory=required)
    assert plan.ordered_presentations == ('due','obligation','b','a')
    # A one-slot quota still owed to a is not silently reassigned by a request.
    assert choose((a,b),global_budget=1,priority=('b','b')).introductions == (a,)
