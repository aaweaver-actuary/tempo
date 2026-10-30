"""Pure presentation regressions; no fixture/database/provider is required."""
from copy import deepcopy
from collections import Counter

import pytest

from tempo_learning_runtime import queue_ordering as ordering


def rows_for(*groups):
    rows = []
    for cohort, kind, count in groups:
        for _ in range(count):
            identifier = len(rows) + 1
            rows.append(dict(id=identifier, card_id=f"card-{identifier}", position=identifier - 1,
                             content_type=kind, admission_kind=cohort, gameplay_priority_reason=None))
    return rows


def plan(rows, seed=0, **options):
    return ordering._plan_daily_queue_order(rows, "2026-10-18", {"seed": seed, "membership_hash": "old"}, **options)


def is_new_opening(row):
    return row['admission_kind'] == 'new' and (row['content_type'] or 'opening') == 'opening'


@pytest.mark.parametrize("new_count,review_count", [(10, 90), (90, 10), (1, 99)])
def test_new_openings_precede_ordinary_reviews_for_skewed_cohorts(new_count, review_count):
    rows = rows_for(("review", "opening", review_count), ("new", "opening", new_count))
    for seed in range(64):
        ordered = plan(rows, seed)[2]
        assert all(is_new_opening(row) for row in ordered[:new_count])
        assert all(not is_new_opening(row) for row in ordered[new_count:])


@pytest.mark.parametrize("groups", [
    [("new", "opening", 10), ("review", "tactic", 90)],
    [("new", "opening", 90), ("new", "tactic", 10), ("review", "opening", 30)],
    [("new", "opening", 1), ("review", "tactic", 1), ("review", "defense", 1)],
    [("review", "middlegame", 7)], [],
])
def test_new_opening_front_block_preserves_membership_seed_and_input(groups):
    rows = rows_for(*groups)
    original = deepcopy(rows)
    expected = Counter((row['admission_kind'], row['content_type']) for row in rows)
    for seed in range(16):
        result = plan(rows, seed)
        assert result == plan(list(reversed(rows)), seed)
        assert Counter((row['admission_kind'], row['content_type']) for row in result[2]) == expected
        assert {row['id'] for row in result[2]} == {row['id'] for row in rows}
        assert result[0] == seed
        assert result[1].startswith(ordering.PRESENTATION_VERSION + ':')
        count = sum(is_new_opening(row) for row in rows)
        assert all(is_new_opening(row) for row in result[2][:count])
        assert all(not is_new_opening(row) for row in result[2][count:])
        assert plan(result[2], seed)[2] == result[2]
    assert rows == original


def test_ordinary_remainder_keeps_existing_cohort_and_category_spreading():
    rows = rows_for(("new", "opening", 9), ("new", "tactic", 10), ("review", "opening", 20),
                    ("review", "tactic", 20))
    for seed in range(16):
        suffix = plan(rows, seed)[2][9:]
        # Promotion retains the baseline mix rather than putting new tactics in
        # the opening block or sorting ordinary work back to its input positions.
        assert sum(row['admission_kind'] == 'new' for row in suffix) == 10
        assert {row['content_type'] for row in suffix[:10]} == {'opening', 'tactic'}
        assert {row['admission_kind'] for row in suffix[:10]} == {'new', 'review'}
        assert suffix != rows[9:]


def test_frozen_relative_rank_moves_across_categories_without_changing_selection():
    rows = rows_for(("new", "opening", 9), ("review", "tactic", 3))
    for rank, row in enumerate(reversed(rows[:9]), 1):
        row.update(frozen_rank=rank, frozen_valid=True)
    for seed in range(8):
        ordered = plan(rows, seed)[2]
        assert [row['frozen_rank'] for row in ordered if row['content_type'] == 'opening'] == list(range(1, 10))
        assert all(is_new_opening(row) for row in ordered[:9])
        assert sorted(row['id'] for row in ordered) == list(range(1, 13))


@pytest.mark.parametrize("protection", [dict(issued=True), dict(presented=True), dict(taught=True),
    dict(cycle=1), dict(attempt_failed=True), dict(attempt_state='guided'), dict(attempt_state='reinforcement'),
    dict(review_completed=True), dict(eligible=False), dict(frozen_valid=False), dict(admission_kind='explicit')])
def test_issued_retry_explicit_and_unavailable_positions_never_move(protection):
    rows = rows_for(("new", "opening", 12), ("review", "tactic", 4))
    rows[3].update(protection)
    for seed in range(8):
        ordered = plan(rows, seed)[2]
        assert ordered[3]['id'] == rows[3]['id']
        assert plan(ordered, seed)[2] == ordered


def test_priority_is_first_movable_and_overlay_preserves_ordinary_order():
    rows = rows_for(("new", "opening", 9), ("review", "tactic", 3))
    rows[0]['issued'] = True
    rows[-1]['gameplay_priority_reason'] = ordering.MISS_REASON
    mixed = plan(rows)[2]
    assert mixed[0]['id'] == rows[0]['id'] and mixed[1]['id'] == rows[-1]['id']
    overlay = ordering._priority_queue_order(rows)
    assert overlay[:2] == [rows[0], rows[-1]]
    assert overlay[2:] == rows[1:-1]


def test_preserve_through_retains_frozen_rank_order_within_existing_openings():
    rows = rows_for(("new", "opening", 4), ("review", "tactic", 4))
    rows[0]['position'], rows[3]['position'] = rows[3]['position'], rows[0]['position']
    ordered = plan(rows, preserve_through_entry_id=4)[2]
    for slot in range(4):
        assert ordered[slot] == next(row for row in rows if row['position'] == slot)


def test_fingerprint_replays_saved_seed_and_preserves_only_an_existing_mix():
    rows = rows_for(("new", "opening", 3), ("new", "tactic", 2))
    fingerprint = plan(rows, 123)[1]
    saved = {'seed': 123, 'membership_hash': fingerprint}
    assert ordering._plan_daily_queue_order(rows, '2099-01-01', saved)[0] == 123
    assert ordering._preserved_queue_fingerprint(rows, saved, [None, 0]) == fingerprint
    assert ordering._preserved_queue_fingerprint(rows, None, [None, 0]).startswith('unmixed:')
    frozen = ordering._queue_order_fingerprint(fingerprint, ['frozen', 2])
    assert frozen.startswith(ordering.PRESENTATION_VERSION + ':') and frozen != fingerprint
    assert ordering._queue_order_fingerprint(fingerprint, ['frozen', 3]) != frozen


def test_prepared_presentation_state_fences_issuance_positions_and_eligibility():
    rows = rows_for(("new", "opening", 3))
    before = ordering._queue_presentation_state(rows)
    for field, value in [('issued', True), ('position', 90), ('eligible', False), ('revision', 2)]:
        changed = deepcopy(rows)
        changed[0][field] = value
        assert ordering._queue_presentation_state(changed) != before


def test_fresh_urgent_admission_after_prior_cycle_is_not_a_positioned_retry():
    rows = rows_for(("review", "opening", 4))
    rows[0]['issued'] = True
    rows[-1].update(cycle=2, attempt_state='gameplay', gameplay_priority_reason=ordering.MISS_REASON)
    assert ordering._priority_queue_order(rows)[:2] == [rows[0], rows[-1]]
    assert plan(rows)[2][:2] == [rows[0], rows[-1]]
    for state in ('guided', 'reinforcement'):
        rows[-1]['attempt_state'] = state
        assert ordering._priority_queue_order(rows)[-1] == rows[-1]
    rows[-1]['attempt_state'] = 'gameplay'
    for protection in ('issued','presented','attempt_failed','review_completed'):
        rows[-1][protection] = True
        assert ordering._priority_queue_order(rows)[-1] == rows[-1]
        rows[-1][protection] = False


def test_current_receipt_cannot_certify_replacement_activation_or_unmixed_append():
    rows = rows_for(("new", "opening", 3))
    token = ['old', 1]
    saved = {'membership_hash': ordering._queue_order_fingerprint(ordering._queue_membership_hash(rows), token)}
    assert ordering._preserved_queue_fingerprint(rows[1:], saved, ['replacement', 2]).startswith('unmixed:')
    appended = rows + [{**rows[-1], 'id': 4, 'card_id': 'new-admission'}]
    assert ordering._preserved_queue_fingerprint(appended, saved, token).startswith('unmixed:')


def test_only_verified_pre_mutation_provenance_preserves_removal_and_positioned_retry():
    rows = rows_for(("new", "opening", 3))
    token = ['active', 1]
    saved = {'membership_hash': ordering._queue_order_fingerprint(ordering._queue_membership_hash(rows), token)}
    previous = ordering._queue_order_provenance(rows, saved, token)
    changed = rows[1:] + [{**rows[0], 'id': 4, 'cycle': 1, 'attempt_state': 'guided'}]
    expected = ordering._queue_order_fingerprint(ordering._queue_membership_hash(changed), token)
    assert ordering._preserved_queue_fingerprint(changed, saved, token, previous=previous) == expected
    assert ordering._preserved_queue_fingerprint(changed, saved, ['replacement', 2], previous=previous).startswith('unmixed:')
    assert ordering._queue_order_provenance(rows[1:], saved, token) is None


def test_null_content_normalizes_to_new_opening_but_new_tactics_do_not():
    rows = rows_for(("review", "opening", 5), ("new", "tactic", 4), ("new", None, 2))
    ordered = plan(rows)[2]
    assert ordered[:2] == rows[-2:]
    assert not any(is_new_opening(row) for row in ordered[2:])


def test_ranked_openings_precede_unranked_then_use_prior_position_and_id():
    rows = rows_for(("new", "opening", 6), ("review", "opening", 2))
    rows[0]['frozen_rank'] = 4
    rows[2]['frozen_rank'] = 0
    rows[3]['frozen_rank'] = 4
    rows[1]['position'] = rows[4]['position'] = 6
    for seed in range(8):
        ordered = plan(rows, seed)[2]
        assert [row['id'] for row in ordered[:6]] == [3, 1, 4, 6, 2, 5]


def test_empty_and_all_protected_queues_preserve_exact_positions():
    assert plan([])[2] == []
    rows = rows_for(("review", "opening", 3), ("new", "opening", 4))
    for row in rows:
        row['issued'] = True
    assert plan(list(reversed(rows)))[2] == rows


def test_no_promotable_openings_keep_the_versioned_baseline_order():
    rows = rows_for(("review", "opening", 20), ("new", "tactic", 4))
    for seed in range(8):
        result = plan(rows, seed)[2]
        assert result == plan(list(reversed(rows)), seed)[2]
        assert result == plan(result, seed)[2]
        # With every old row inside the preservation boundary, baseline order
        # itself is fixed and the final pass must not reorder the remainder.
        assert plan(rows, seed, preserve_through_entry_id=24)[2] == rows


def test_new_opening_fills_first_available_slot_after_issued_ordinary_head():
    rows = rows_for(("review", "opening", 5), ("new", "opening", 2))
    rows[0]['issued'] = True
    ordered = plan(rows)[2]
    assert ordered[0] == rows[0]
    assert ordered[1:3] == rows[-2:]


@pytest.mark.parametrize('protection', [dict(cycle=1), dict(attempt_failed=True),
    dict(attempt_state='guided'), dict(attempt_state='reinforcement')])
def test_positioned_repeat_stays_while_new_opening_crosses_ordinary_reviews(protection):
    rows = rows_for(("review", "opening", 5), ("new", "opening", 2))
    rows[2].update(protection)
    ordered = plan(rows, preserve_through_entry_id=5)[2]
    assert ordered[2] == rows[2]
    assert ordered[:2] == rows[-2:]
    assert ordered[3:] == [rows[0], rows[1], rows[3], rows[4]]


def test_new_child_crosses_preserve_through_untouched_reviews_without_reversing_them():
    rows = rows_for(("review", "opening", 2), ("new", "opening", 1))
    assert plan(rows, preserve_through_entry_id=2)[2] == [rows[2], rows[0], rows[1]]


def test_new_child_and_prior_opening_use_frozen_rank_across_preserved_ordinary_work():
    rows = rows_for(("review", "opening", 4), ("new", "opening", 2))
    rows[0]['issued'] = True
    rows[2].update(cycle=1, attempt_state='guided')
    rows[4]['frozen_rank'] = 9
    rows[5]['frozen_rank'] = 1
    assert plan(rows, preserve_through_entry_id=5)[2] == [rows[0], rows[5], rows[2], rows[4], rows[1], rows[3]]


def test_historical_literal_v3_receipt_cannot_certify_current_policy():
    rows = rows_for(("new", "opening", 3))
    old = {'seed': 123, 'membership_hash': 'queue-presentation-v3:' + ordering._digest(
        sorted((row['id'], row['card_id']) for row in rows))}
    assert ordering.PRESENTATION_VERSION != 'queue-presentation-v3'
    assert ordering._queue_order_provenance(rows, old, [None, 0]) is None
    assert ordering._preserved_queue_fingerprint(rows, old, [None, 0]).startswith('unmixed:')


def test_urgent_game_work_precedes_new_openings_across_preserve_through():
    rows = rows_for(("review", "opening", 4), ("new", "opening", 2))
    rows[0]['issued'] = True
    rows[3]['gameplay_priority_reason'] = ordering.MISS_REASON
    ordered = plan(rows, preserve_through_entry_id=4)[2]
    assert ordered[:4] == [rows[0], rows[3], rows[4], rows[5]]
    assert ordered[4:] == rows[1:3]
