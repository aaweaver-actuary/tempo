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


@pytest.mark.parametrize("minority", [("new", "tactic"), ("review", "opening")])
def test_ten_percent_cohort_is_spread_through_the_whole_queue(minority):
    rows = rows_for(("new", "opening", 90), (*minority, 10))
    for seed in range(64):
        ordered = plan(rows, seed)[2]
        positions = [index + 1 for index, row in enumerate(ordered)
                     if (row['admission_kind'], row['content_type']) == minority]
        assert len(positions) == 10
        assert positions[0] <= 8 and positions[-1] >= 92
        assert all(9 <= right - left <= 11 for left, right in zip(positions, positions[1:]))


@pytest.mark.parametrize("groups", [
    [("new", "opening", 10), ("review", "tactic", 90)],
    [("new", "opening", 90), ("new", "tactic", 10), ("review", "opening", 30)],
    [("new", "opening", 1), ("review", "tactic", 1), ("review", "defense", 1)],
    [("review", "middlegame", 7)], [],
])
def test_proportional_prefix_counts_and_membership_are_stable_across_seeds(groups):
    rows = rows_for(*groups)
    original = deepcopy(rows)
    expected = Counter((row['admission_kind'], row['content_type']) for row in rows)
    for seed in range(16):
        result = plan(rows, seed)
        assert result == plan(list(reversed(rows)), seed)
        assert {row['id'] for row in result[2]} == {row['id'] for row in rows}
        assert result[0] == seed
        assert result[1].startswith(ordering.PRESENTATION_VERSION + ':')
        seen = Counter()
        for length, row in enumerate(result[2], 1):
            seen[row['admission_kind'], row['content_type']] += 1
            for key, count in expected.items():
                assert abs(seen[key] - length * count / len(rows)) <= len(expected)
    assert rows == original


def test_frozen_relative_rank_moves_across_categories_without_changing_selection():
    rows = rows_for(("new", "opening", 9), ("review", "tactic", 3))
    for rank, row in enumerate(reversed(rows[:9]), 1):
        row.update(frozen_rank=rank, frozen_valid=True)
    for seed in range(8):
        ordered = plan(rows, seed)[2]
        assert [row['frozen_rank'] for row in ordered if row['content_type'] == 'opening'] == list(range(1, 10))
        assert any(row['content_type'] == 'tactic' for row in ordered[:8])
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


def test_preserve_through_retains_exact_existing_slots():
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
        # #622: an unissued retry flag does not pin ordinary positions ahead
        # of an urgent target. Actual issued/evidence identities below do.
        assert ordering._priority_queue_order(rows)[:2] == [rows[0], rows[-1]]
    rows[-1]['attempt_state'] = 'gameplay'
    for protection in ('issued','presented','review_completed'):
        rows[-1][protection] = True
        assert ordering._priority_queue_order(rows)[-1] == rows[-1]
        rows[-1][protection] = False


@pytest.mark.parametrize('old_flag', [dict(cycle=2, attempt_state='guided'),
    dict(cycle=1, attempt_state='reinforcement'), dict(taught=True), dict(attempt_failed=True)])
def test_urgent_target_precedes_unissued_historical_flags_but_preserves_actual_attempt(old_flag):
    rows = rows_for(('new', 'opening', 5))
    rows[0]['issued'] = True
    rows[1].update(old_flag)
    rows[-1].update(gameplay_priority_reason=ordering.MISS_REASON,
        urgent_stream='forward', urgent_determined_at='2026-10-30T00:00:00Z', urgent_effect_id='effect')
    assert ordering._priority_queue_order(rows)[:2] == [rows[0], rows[-1]]
    assert plan(rows, preserve_through_entry_id=rows[-2]['id'])[2][:2] == [rows[0], rows[-1]]
    assert rows[1].items() >= old_flag.items()  # Moving is not rewriting attempt state.


def test_live_urgent_cohort_is_durable_fifo_before_history_and_ordinary_work():
    rows = rows_for(('review', 'opening', 10))
    rows[0]['issued'] = True
    # Deliberately reverse card IDs and queue positions relative to determination.
    for ordinal, row in enumerate(reversed(rows[2:9])):
        row.update(gameplay_priority_reason=ordering.MISS_REASON, urgent_stream='forward',
            urgent_determined_at=f'2026-10-30T00:00:0{ordinal}Z', urgent_effect_id=f'effect-{ordinal}')
    rows[1].update(gameplay_priority_reason=ordering.MISS_REASON, urgent_stream='reconciliation',
        urgent_determined_at='2020-01-01T00:00:00Z', urgent_effect_id='history')
    expected = [rows[0], *reversed(rows[2:9]), rows[1], rows[-1]]
    assert ordering._priority_queue_order(rows) == expected
    assert ordering._priority_queue_order(list(reversed(rows))) == expected
    assert plan(rows)[2] == expected
    # A later arrival cannot leapfrog the cohort and a second projection cannot
    # restart its ordering. More than five targets remain actual FIFO backlog.
    rows[-1].update(gameplay_priority_reason=ordering.MISS_REASON, urgent_stream='forward',
        urgent_determined_at='2026-10-30T00:00:09Z', urgent_effect_id='later')
    assert ordering._priority_queue_order(rows)[1:8] == list(reversed(rows[2:9]))
    assert ordering._priority_queue_order(ordering._priority_queue_order(rows)) == ordering._priority_queue_order(rows)


def test_urgent_fifo_compares_actual_utc_instants_at_microsecond_precision():
    rows = rows_for(('review','opening',3))
    for row in rows:
        row.update(gameplay_priority_reason=ordering.MISS_REASON,urgent_stream='forward')
    rows[0].update(urgent_determined_at='2026-10-30T00:00:00.000001Z',urgent_effect_id='b')
    rows[1].update(urgent_determined_at='2026-10-30T00:00:00.000001+00:00',urgent_effect_id='a')
    rows[2].update(urgent_determined_at='2026-10-30T00:00:00.000002Z',urgent_effect_id='0')
    assert ordering._priority_queue_order(rows) == [rows[1],rows[0],rows[2]]


def test_unavailable_priority_does_not_relax_ordinary_positioned_repeat():
    rows = rows_for(('review','opening',6))
    rows[2].update(cycle=1,attempt_state='guided')
    rows[-1].update(eligible=False)
    ordinary = plan(rows,1)[2]
    rows[-1]['gameplay_priority_reason'] = ordering.MISS_REASON
    held_only = plan(rows,1)[2]
    assert held_only == ordinary
    assert held_only[2] == rows[2]


def test_prior_v3_receipt_cannot_certify_replacement_activation_or_unmixed_append():
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
