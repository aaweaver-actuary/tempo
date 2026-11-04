"""Cutover F03/F11 policy evidence; does not activate or migrate a cohort."""
from dataclasses import replace

from app.study.recombination.models import CleanStreak
from app.study.recombination.policy import plan_join, reduce_clean_streak
from test_product_recombination_policy import FrozenRoute, codes, context, member, review, streak


def test_f03_legacy_failure_and_split_require_new_evidence():
    before = streak("A")
    failure = review(3, outcome="again")
    after = reduce_clean_streak(before, failure)
    assert before.eligible and after.count == 0
    assert failure.review_id in after.completed_review_ids
    fragment = CleanStreak("A", 1, "split:original-command")
    assert reduce_clean_streak(fragment, review(4)) is fragment
    for index in range(3):
        fragment = reduce_clean_streak(fragment, review(index, evidence_epoch="split:original-command"))
    assert fragment.eligible
    assert before.completed_review_ids == frozenset(f"A:review:{i}" for i in range(3))


def test_f11_italian_active_teaching_and_uncertain_source_hold_original_carrier():
    route = replace(FrozenRoute(), route_id="italian", moves=("e2e4", "e7e5", "g1f3", "b8c6", "f1c4"))
    first = member("italian-original", 0, 1, route=route)
    teaching = member("italian-teaching", 2, 3, route=route, active_attempt=True)
    held = plan_join((first, teaching), context(route))
    assert held.plan is None and "active_attempt" in codes(held)
    assert first.presentation.presentation_id == "italian-original"
    assert first.streak.completed_review_ids == frozenset(f"italian-original:review:{i}" for i in range(3))
    uncertain = plan_join((first, replace(teaching, active_attempt=False)), context(route, complete=False))
    assert uncertain.plan is None
