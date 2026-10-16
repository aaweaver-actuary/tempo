from dataclasses import replace
import pytest
from tempo_learning_remediation import Candidate, evaluate_eligibility, order_candidates, propose_admission

BASE=Candidate("card",1,"publication",1,"route",False,True,progression_allowed=False,remaining_allowance=0)


@pytest.mark.parametrize("allowance",[0,-1,1])
def test_game_miss_is_explicit_priority_and_preserves_existing_cap_progression_exception(allowance):
    candidate=replace(BASE,remaining_allowance=allowance)
    assert evaluate_eligibility(candidate).eligible
    proposal=propose_admission(candidate)
    assert proposal.priority == "real_game_miss" and proposal.path == "introduction"


@pytest.mark.parametrize("field,reason",[("deleted","card_deleted"),("retired","card_retired"),
    ("content_valid","content_invalid"),("current_revision","stale_revision"),("buried","buried_today")])
def test_gameplay_exception_never_bypasses_validity_or_lifecycle(field,reason):
    candidate=replace(BASE,**{field:False if field in {"content_valid","current_revision"} else True})
    assert evaluate_eligibility(candidate).reason == reason
    assert propose_admission(candidate) is None


def test_ordinary_cards_keep_quota_and_progression_requirements():
    ordinary=replace(BASE,outstanding_miss=False)
    assert evaluate_eligibility(ordinary).reason == "curriculum_blocked"
    assert evaluate_eligibility(replace(ordinary,progression_allowed=True)).reason == "allowance_exhausted"


def test_future_due_studied_card_uses_existing_review_promotion_path():
    assert propose_admission(replace(BASE,introduced=True)).path == "review"


def test_priority_overlay_deduplicates_shared_cards_and_preserves_head_and_ordinary_frozen_order():
    head=replace(BASE,card_id="head",protected=True,outstanding_miss=False)
    ordinary=tuple(replace(BASE,card_id=name,outstanding_miss=False) for name in ["z","a","m"])
    misses=tuple(replace(BASE,card_id=name) for name in ["miss-b","miss-a","miss-b"])
    ordered=order_candidates((head,*ordinary,*misses))
    assert [value.card_id for value in ordered] == ["head","miss-a","miss-b","z","a","m"]
    assert propose_admission(head) is None


def test_shared_card_uses_valid_alias_without_losing_frozen_card_position():
    blocked = replace(BASE, route_id="blocked", content_valid=False)
    valid = replace(BASE, route_id="valid")
    assert order_candidates((blocked, valid)) == (valid,)


@pytest.mark.parametrize("miss_first", [False, True])
def test_eligible_miss_duplicate_preserves_priority_and_route_in_either_order(miss_first):
    ordinary = replace(BASE, outstanding_miss=False, progression_allowed=True, remaining_allowance=1)
    first = replace(ordinary, card_id="a")
    duplicate = replace(ordinary, card_id="z", route_id="ordinary-route")
    miss = replace(duplicate, outstanding_miss=True, route_id="miss-route")
    representations = (miss, duplicate) if miss_first else (duplicate, miss)
    assert order_candidates((first, *representations)) == (miss, first)
    assert propose_admission(order_candidates((first, *representations))[0]).priority == "real_game_miss"


@pytest.mark.parametrize("blocked", [dict(current_revision=False), dict(content_valid=False), dict(buried=True)])
@pytest.mark.parametrize("miss_first", [False, True])
def test_ineligible_miss_duplicate_never_displaces_eligible_ordinary_route(blocked, miss_first):
    ordinary = replace(BASE, outstanding_miss=False, progression_allowed=True, remaining_allowance=1)
    miss = replace(BASE, route_id="blocked-miss-route", **blocked)
    representations = (miss, ordinary) if miss_first else (ordinary, miss)
    assert order_candidates(representations) == (ordinary,)


@pytest.mark.parametrize("miss_first", [False, True])
def test_miss_duplicate_cannot_replace_protected_issued_head(miss_first):
    head = replace(BASE, outstanding_miss=False, protected=True, route_id="issued-route")
    miss = replace(BASE, route_id="miss-route")
    representations = (miss, head) if miss_first else (head, miss)
    assert order_candidates(representations) == (head,)
    assert propose_admission(head) is None


def test_eligible_ordinary_duplicates_keep_first_route_and_frozen_card_order():
    ordinary = replace(BASE, outstanding_miss=False, progression_allowed=True, remaining_allowance=1)
    candidates = tuple(replace(ordinary, card_id=name) for name in ("z", "a", "m"))
    assert order_candidates((*candidates, replace(candidates[0], route_id="later-route"))) == candidates


def test_single_card_preparation_advances_one_cursor_without_skipping_unapplied_work():
    from tempo_learning_remediation import WorkItem, WorkPage, prepare_admissions
    calls=[]
    class Repository:
        def pending_page(self,cursor,*,limit):
            calls.append((cursor,limit))
            return WorkPage((WorkItem("card",1,"pending",None),),"card",True)
        def has_current_miss(self,candidate): return True
    class Facts:
        def candidates(self,items): return (BASE,)
    prepared=prepare_admissions(Repository(),Facts(),page_size=1,max_pages=1)
    assert calls==[("",1)]
    assert prepared.candidates==(BASE,) and prepared.cursor=="card" and prepared.has_more
    assert prepared.examined_pages==1


@pytest.mark.parametrize("page_size,max_pages",[(0,1),(17,1),(1,0),(1,5)])
def test_smaller_preparation_cannot_expand_existing_module_bounds(page_size,max_pages):
    from tempo_learning_remediation import prepare_admissions
    with pytest.raises(ValueError,match="bounds"):
        prepare_admissions(None,None,page_size=page_size,max_pages=max_pages)


def test_correlated_current_miss_predicate_retains_frontier_fence_and_rejects_sql_text():
    from tempo_learning_remediation import current_miss_sql
    predicate=current_miss_sql("accepted_card")
    assert predicate.count("%s")==1
    assert "head.card_id=accepted_card.id" in predicate and "OFFSET 0" in predicate
    assert "contribution.card_revision=accepted_card.revision" in predicate
    with pytest.raises(ValueError): current_miss_sql("card; DROP TABLE cards")
