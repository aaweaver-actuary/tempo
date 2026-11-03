from datetime import date, datetime, timezone

from database.tooling.position_study_metrics import (
    AcceptedPositionEvent,
    AttemptReceipt,
    MAX_ATTEMPT_RECEIPTS,
    MAX_EVENTS_PER_ATTEMPT,
    summarize,
)


def event(event_id, position, grade, due, day=date(2026, 11, 2), repertoire="r1", at=None):
    return AcceptedPositionEvent(event_id, repertoire, position, grade, due, at, day)


def test_aliases_skips_repeated_moves_and_receipt_duplicates_are_counted_correctly():
    events = (
        event("e1", "fen-a", "good", True),
        event("e2", "fen-a", "again", False),  # two route aliases, same key
        event("e3", "fen-b", "pending", False),  # skipped/pending is not tested
        event("e4", "fen-c", "again", False),  # nondue first wrong is included
    )
    receipt = AttemptReceipt(
        "a1", None, None, events, repeated_moves_avoided=3,
        duplicate_attempts_suppressed=2, prefix_holds=1, fallback_count=1,
        queue_latency_ms=40, review_save_latency_ms=80,
    )
    result = summarize((receipt, receipt), None, None)
    assert result.distinct_positions_tested == 2
    assert result.actual_response_events == 3
    assert result.nondue_first_wrong == 2
    assert result.due_good == 1
    assert result.positive_positions_per_day == 1
    assert result.repeated_moves_avoided == 3
    assert result.duplicate_attempts_suppressed == 2
    assert result.prefix_holds == 1
    assert result.fallback_count == 1
    assert result.queue_latency_ms_mean == 40
    assert result.review_save_latency_ms_mean == 80


def test_two_independent_good_events_count_as_events_but_one_positive_per_day():
    first = event("e1", "fen-a", "good", True)
    second = event("e2", "fen-a", "good", True)
    result = summarize((AttemptReceipt("a1", None, None, (first,)),
                        AttemptReceipt("a2", None, None, (second,))), None, None)
    assert result.actual_response_events == 2
    assert result.due_good == 2
    assert result.positive_positions_per_day == 1


def test_missing_authoritative_study_day_makes_positive_per_day_unavailable():
    result = summarize((AttemptReceipt(
        "a", None, None, (event("e", "fen", "good", True, day=None),)
    ),), None, None)
    assert result.actual_response_events == 1
    assert result.positive_positions_per_day is None


def test_nondue_correct_observe_counts_as_tested_but_not_due_good():
    start = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 12, 10, tzinfo=timezone.utc)
    observation = event("observe-1", "fen-a", "observe", False,
                        at=datetime(2026, 11, 2, 12, 4, tzinfo=timezone.utc))
    result = summarize((AttemptReceipt("a", start, end, (observation,)),), start, end)
    assert result.distinct_positions_tested == 1
    assert result.actual_response_events == 1
    assert result.due_good == 0
    assert result.due_again == 0
    assert result.nondue_first_wrong == 0
    assert result.distinct_positions_per_minute == 0.1


def test_elapsed_efficiency_requires_valid_window_and_retention_needs_real_observations():
    receipt = AttemptReceipt("a", None, None, (event("e", "fen", "good", True),))
    result = summarize((receipt,), None, datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert result.elapsed_minutes is None
    assert result.distinct_positions_per_minute is None
    assert result.retention_7d is None
    assert result.retention_30d is None
    start = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 12, 10, tzinfo=timezone.utc)
    missing_event_time = event("e2", "fen", "good", True, at=None)
    result = summarize((AttemptReceipt("a2", None, None, (missing_event_time,)),), start, end)
    assert result.distinct_positions_tested == 1
    assert result.elapsed_minutes == 10
    assert result.distinct_positions_per_minute is None


def test_naive_or_nonpositive_window_does_not_claim_elapsed_efficiency():
    receipt = AttemptReceipt("a", None, None, (event("e", "fen", "good", True),))
    naive_start = datetime(2026, 11, 2, 12)
    naive_end = datetime(2026, 11, 2, 12, 10)
    assert summarize((receipt,), naive_start, naive_end).distinct_positions_per_minute is None
    aware_start = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    assert summarize((receipt,), aware_start, aware_start).elapsed_minutes is None


def test_efficiency_uses_distinct_repertoire_position_over_positive_elapsed_time():
    start = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 12, 10, tzinfo=timezone.utc)
    middle = datetime(2026, 11, 2, 12, 5, tzinfo=timezone.utc)
    events = (event("e1", "fen-a", "good", True, at=middle),
              event("e2", "fen-a", "again", False, at=middle),
              event("e3", "fen-a", "good", True, repertoire="r2", at=middle))
    result = summarize((AttemptReceipt("a", start, end, events),), start, end)
    assert result.distinct_positions_tested == 2
    assert result.elapsed_minutes == 10
    assert result.distinct_positions_per_minute == 0.2


def test_out_of_window_event_is_rejected_instead_of_distorting_throughput():
    start = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 12, 10, tzinfo=timezone.utc)
    outside = event("e", "fen", "good", True,
                    at=datetime(2026, 11, 2, 12, 11, tzinfo=timezone.utc))
    try:
        summarize((AttemptReceipt("a", None, None, (outside,)),), start, end)
    except ValueError as error:
        assert "outside the measurement window" in str(error)
    else:
        raise AssertionError("out-of-window event was included")


def test_unknown_donor_migration_and_latency_data_are_explicitly_unavailable():
    result = summarize((AttemptReceipt("a", None, None),), None, None)
    assert result.donor_eligible is None
    assert result.migration_excluded is None
    assert result.queue_latency_samples == 0
    assert result.queue_latency_ms_mean is None
    assert result.review_save_latency_samples == 0
    assert result.review_save_latency_ms_mean is None
    assert result.repeated_moves_avoided is None
    assert result.duplicate_attempts_suppressed is None
    assert result.prefix_holds is None
    assert result.fallback_count is None


def test_input_page_and_per_attempt_events_have_hard_bounds():
    receipt = AttemptReceipt("a", None, None)
    try:
        summarize(tuple(receipt for _ in range(MAX_ATTEMPT_RECEIPTS + 1)), None, None)
    except ValueError as error:
        assert "receipt page exceeds" in str(error)
    else:
        raise AssertionError("oversized receipt page was accepted")
    too_many_events = tuple(event(str(i), f"fen-{i}", "good", True)
                            for i in range(MAX_EVENTS_PER_ATTEMPT + 1))
    try:
        summarize((AttemptReceipt("b", None, None, too_many_events),), None, None)
    except ValueError as error:
        assert "attempt exceeds" in str(error)
    else:
        raise AssertionError("oversized attempt was accepted")
