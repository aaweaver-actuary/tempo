"""Within-session retry decisions use explicit facts, time, and saved draws."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tempo_learning_review.models import QueueAttempt
from tempo_learning_review.retry_policy import (
    FailedRetryRequest, QueueIdentity, RetryCandidate, QueueReadyFacts,
    choose_queue_action, plan_failed_retry, retry_order_key,
    retry_insertion_order,
)

NOW = datetime(2026, 10, 18, 23, 58, tzinfo=timezone.utc)
SOURCE = QueueAttempt(10, "card", "2026-10-18", 2, "review", 3, True, "queued", None)
REQUEST = FailedRetryRequest(42, SOURCE, NOW)


def identity(entry=11, *, issued=False):
    return QueueIdentity(entry, f"card-{entry}", 3, 3, "2026-10-18", issued)


def retry(entry=11, *, seconds=450, token=1, urgent=False):
    return RetryCandidate(identity(entry), NOW + timedelta(seconds=seconds), f"{token:032x}", urgent)


@pytest.mark.parametrize("seconds", [300, 450, 600])
def test_failed_retry_deadline_uses_original_review_time_and_saved_draw(seconds):
    plan = plan_failed_retry(REQUEST, delay_seconds=seconds, order_token="0" * 32)
    assert plan.policy_version == "soft-failed-retry-v1"
    assert plan.ready_at == NOW + timedelta(seconds=seconds)
    assert plan.delay_seconds == seconds and plan.order_token == "0" * 32
    assert plan_failed_retry(REQUEST, delay_seconds=seconds, order_token="0" * 32) == plan


@pytest.mark.parametrize("seconds", [0, 299, 601, 900, 300.5, True])
def test_failed_retry_rejects_out_of_policy_delay(seconds):
    with pytest.raises(ValueError, match="300.*600"):
        plan_failed_retry(REQUEST, delay_seconds=seconds, order_token="0" * 32)


@pytest.mark.parametrize("token", ["", "abcd", "z" * 32, "0" * 33])
def test_failed_retry_requires_a_persistable_order_token(token):
    with pytest.raises(ValueError, match="token"):
        plan_failed_retry(REQUEST, delay_seconds=450, order_token=token)


def test_midnight_and_delayed_delivery_do_not_reset_retry_deadline():
    plan = plan_failed_retry(REQUEST, delay_seconds=600, order_token="0" * 32)
    assert plan.ready_at == datetime(2026, 10, 19, 0, 8, tzinfo=timezone.utc)
    facts = QueueReadyFacts(None, retry(seconds=600), None, True)
    decision = choose_queue_action(facts, observed_at=NOW + timedelta(hours=2))
    assert decision.kind == "activate" and decision.reason == "deadline"


def test_retry_policy_rejects_naive_completion_or_observation_time():
    with pytest.raises(ValueError, match="timezone"):
        plan_failed_retry(replace(REQUEST, reviewed_at=NOW.replace(tzinfo=None)),
                          delay_seconds=450, order_token="0" * 32)
    with pytest.raises(ValueError, match="timezone"):
        choose_queue_action(QueueReadyFacts(None, None, None, True),
                            observed_at=NOW.replace(tzinfo=None))


def test_ready_work_wins_over_cooling_retry_even_with_incomplete_admission():
    head = identity(99)
    action = choose_queue_action(QueueReadyFacts(head, None, retry(), False), observed_at=NOW)
    assert action.kind == "issue" and action.identity == head


def test_an_issued_head_remains_authoritative_when_another_retry_expires():
    head = identity(99, issued=True)
    action = choose_queue_action(QueueReadyFacts(head, retry(seconds=0), None, True), observed_at=NOW)
    assert action.kind == "issue" and action.identity == head


def test_natural_expiry_applies_saved_placement_before_issuing_a_different_head():
    candidate = retry(seconds=0)
    action = choose_queue_action(QueueReadyFacts(identity(99), candidate, None, True), observed_at=NOW)
    assert action.kind == "activate" and action.identity == candidate.identity
    assert action.reason == "deadline"


def test_incomplete_authority_never_releases_a_cooling_retry_or_claims_completion():
    for candidate in [None, retry()]:
        action = choose_queue_action(QueueReadyFacts(None, None, candidate, False), observed_at=NOW)
        assert action.kind == "pending" and action.identity is None


def test_all_cooling_queue_releases_exactly_one_saved_candidate_early():
    candidate = retry()
    action = choose_queue_action(QueueReadyFacts(None, None, candidate, True), observed_at=NOW)
    assert action.kind == "early_retry" and action.identity == candidate.identity
    assert action.reason == "no_ready_work"


def test_empty_authoritative_queue_is_exhausted_without_fabricating_work():
    action = choose_queue_action(QueueReadyFacts(None, None, None, True), observed_at=NOW)
    assert action.kind == "exhausted" and action.identity is None


def test_saved_retry_order_is_input_independent_and_retains_priority_tiers():
    candidates = [retry(11, token=9), retry(12, token=1), retry(13, token=8, urgent=True)]
    assert [row.identity.entry_id for row in sorted(candidates, key=retry_order_key)] == [13, 12, 11]
    assert sorted(reversed(candidates), key=retry_order_key) == sorted(candidates, key=retry_order_key)


@pytest.mark.parametrize("token", [0, 1, 2, 100, 2**128 - 1])
def test_saved_insertion_keeps_other_relative_order_and_membership(token):
    order = retry_insertion_order(((1, False), (2, False), (3, False), (4, False)),
                                  retry_entry_id=4, order_token=f"{token:032x}")
    assert set(order) == {1, 2, 3, 4}
    assert [entry for entry in order if entry != 4] == [1, 2, 3]
    assert order == retry_insertion_order(((1, False), (2, False), (3, False), (4, False)),
                                          retry_entry_id=4, order_token=f"{token:032x}")


def test_urgent_retry_randomizes_only_within_its_existing_priority_tier():
    rows = ((1, True), (2, False), (3, False), (4, True))
    assert retry_insertion_order(rows, retry_entry_id=4, order_token="0" * 32) == (4, 1, 2, 3)
    assert retry_insertion_order(rows, retry_entry_id=4, order_token="0" * 31 + "1") == (1, 4, 2, 3)


def test_single_retry_insertion_and_absent_identity_are_explicit():
    assert retry_insertion_order(((4, False),), retry_entry_id=4, order_token="0" * 32) == (4,)
    with pytest.raises(ValueError, match="identity"):
        retry_insertion_order(((1, False),), retry_entry_id=4, order_token="0" * 32)


def test_released_retry_restoration_keeps_the_original_release_instead_of_redrawing():
    candidate = replace(retry(),released_at=NOW-timedelta(seconds=1))
    action = choose_queue_action(QueueReadyFacts(None,candidate,None,True),observed_at=NOW)
    assert action.kind=='activate' and action.reason is None


def test_queue_selection_application_uses_the_authority_port_without_transport_or_io():
    from tempo_learning_review.retry_policy import QueueContext,QueueSelection,select_queue_head
    calls = []
    facts = QueueReadyFacts(None,None,retry(),True)
    context = QueueContext(NOW,'2026-10-18')
    class Authority:
        def queue_ready_facts(self,received):
            assert received==context
            calls.append('facts')
            return facts
        def apply_queue_action(self,action,received):
            assert received==context and action.kind=='early_retry'
            calls.append('apply')
            return QueueSelection('pending',reason='authority_changed')
    assert select_queue_head(Authority(),context)==QueueSelection('pending',reason='authority_changed')
    assert calls==['facts','apply']
