import json
import unittest
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from tempo_scheduling import schedule_review, unlock_ready

REVIEWED_AT = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
INITIAL_CARD = json.dumps(dict(card_id=1, state=1, step=0, stability=None, difficulty=None,
                              due=REVIEWED_AT.isoformat(), last_review=None))
BASELINE = json.loads((Path(__file__).parent / 'baseline.json').read_text())


def review(outcome, prior=None, **changes):
    inputs = dict(fsrs_card_json=INITIAL_CARD, reviewed_at=REVIEWED_AT, review_day=REVIEWED_AT.date())
    if prior is not None:
        inputs.update(fsrs_card_json=prior.fsrs_card_json, interval_days=prior.interval_days,
            first_correct_at=prior.first_correct_at, reinforcement_pending=prior.reinforcement_pending,
            scheduling_mode=prior.scheduling_mode, hard_correct_streak=prior.hard_correct_streak,
            recent_attempts=list(prior.recent_attempts))
    inputs.update(changes)
    return schedule_review(outcome, **inputs)


def decoded_inputs(case):
    return {**case['inputs'], 'reviewed_at': datetime.fromisoformat(case['inputs']['reviewed_at']),
            'review_day': date.fromisoformat(case['inputs']['review_day'])}


def serialized(result):
    output = asdict(result)
    output['due_date'] = result.due_date.isoformat()
    output['recent_attempts'] = list(result.recent_attempts)
    return output


class SchedulerTests(unittest.TestCase):
    def test_again_is_reshuffled_into_todays_queue(self):
        result = review('again', interval_days=12)
        self.assertEqual(result.interval_days, 0)
        self.assertEqual(result.due_date, REVIEWED_AT.date())
        self.assertTrue(result.requeue_today)
        self.assertEqual(result.requeue_after_cards, 4)

    def test_unlock_requires_stability_across_days(self):
        self.assertTrue(unlock_ready(14, 3, ['correct', 'correct']))
        self.assertFalse(unlock_ready(13, 3, ['correct', 'correct']))
        self.assertFalse(unlock_ready(14, 2, ['correct', 'correct']))
        self.assertFalse(unlock_ready(14, 3, ['correct', 'again']))
        self.assertFalse(unlock_ready(14, 3, ['correct']))

    def test_first_clean_pass_returns_to_end_then_fsrs_schedules(self):
        first = review('correct')
        self.assertTrue(first.requeue_today)
        self.assertIsNone(first.requeue_after_cards)
        second = review('correct', first)
        self.assertFalse(second.requeue_today)
        self.assertEqual(second.interval_days, 1)
        self.assertFalse(second.reinforcement_pending)

    def test_overdue_growth_is_capped(self):
        second = review('correct', review('correct'))
        third = review('correct', second, reviewed_at=REVIEWED_AT + timedelta(days=180))
        self.assertLessEqual(third.interval_days, max(2, round(second.interval_days * 2.5)))
        self.assertLessEqual(third.interval_days, 365)

    def test_hard_track_enters_and_recovers_on_fixed_cadence(self):
        entered = review('again', recent_attempts=['again', 'correct', 'again', 'correct'])
        self.assertEqual(entered.scheduling_mode, 'hard')
        self.assertTrue(entered.suggest_shorter_prefix)
        first = review('correct', entered, first_correct_at='seen')
        self.assertEqual(first.interval_days, 1)
        second = review('correct', first)
        self.assertEqual(second.interval_days, 3)
        third = review('correct', second)
        self.assertEqual(third.scheduling_mode, 'normal')
        self.assertEqual(third.hard_correct_streak, 0)


def test_fixed_time_schedule_matches_golden_output():
    assert [serialized(review(outcome)) for outcome in ('again', 'correct')] == json.loads(
        (Path(__file__).parent / 'golden.json').read_text())


@pytest.mark.parametrize('case', BASELINE, ids=lambda case: case['name'])
def test_explicit_schedule_preserves_baseline(case):
    assert serialized(schedule_review(**decoded_inputs(case))) == case['expected']


def test_installed_scheduling_exports_and_identity():
    import tempo_scheduling
    import sys
    assert tempo_scheduling.__all__ == ['Schedule', 'schedule_review', 'unlock_ready']
    assert not hasattr(tempo_scheduling, 'Scheduler')
    assert 'app' not in sys.modules
    assert 'site-packages' in tempo_scheduling.__file__


def test_invalid_outcome_is_rejected():
    with pytest.raises(ValueError, match='outcome must'):
        review('unknown')


@pytest.mark.parametrize('field', ['reviewed_at', 'review_day', 'fsrs_card_json'])
def test_environmental_inputs_are_required_keyword_arguments(field):
    inputs = decoded_inputs(BASELINE[0])
    del inputs[field]
    with pytest.raises(TypeError, match=field):
        schedule_review(**inputs)
    inputs[field] = None
    with pytest.raises(ValueError, match=field):
        schedule_review(**inputs)


def test_naive_review_time_is_rejected():
    with pytest.raises(ValueError, match='timezone-aware'):
        review('correct', reviewed_at=REVIEWED_AT.replace(tzinfo=None))


@pytest.mark.parametrize('field', ['card_id', 'state', 'step', 'stability', 'difficulty', 'due', 'last_review'])
def test_incomplete_card_state_is_rejected(field):
    card = json.loads(INITIAL_CARD)
    del card[field]
    with pytest.raises(ValueError, match='complete card state'):
        review('correct', fsrs_card_json=json.dumps(card))


@pytest.mark.parametrize('changes', [dict(card_id=None), dict(due=None), dict(due=''),
    dict(due='2026-10-14T12:00:00'), dict(last_review='2026-10-14T12:00:00'),
    dict(last_review=''), dict(state=None)])
def test_invalid_explicit_card_state_is_rejected(changes):
    with pytest.raises(ValueError, match='invalid explicit fsrs_card_json'):
        review('correct', fsrs_card_json=json.dumps({**json.loads(INITIAL_CARD), **changes}))


@pytest.mark.parametrize('serialized_card', ['', 'null', '[]', '{}', 'malformed'])
def test_missing_serialized_card_has_no_initial_state_fallback(serialized_card):
    with pytest.raises(ValueError, match='invalid explicit fsrs_card_json'):
        review('correct', fsrs_card_json=serialized_card)


@pytest.mark.parametrize('offset', [-7, -4, 5.5, 9])
def test_aware_offsets_preserve_instant_and_separate_local_day(offset):
    local_time = REVIEWED_AT.astimezone(timezone(timedelta(hours=offset)))
    different_day = date(2026, 10, 13)
    assert review('correct', reviewed_at=local_time, review_day=different_day) == review(
        'correct', review_day=different_day)
    assert review('correct', reviewed_at=local_time, review_day=different_day).due_date == different_day


def test_existing_card_offset_timestamps_do_not_change_effective_review_instant():
    card = json.loads(BASELINE[3]['inputs']['fsrs_card_json'])
    for field in ('due', 'last_review'):
        card[field] = datetime.fromisoformat(card[field]).astimezone(timezone(timedelta(hours=9))).isoformat()
    inputs = decoded_inputs(BASELINE[3])
    assert schedule_review(**{**inputs, 'fsrs_card_json': json.dumps(card)}) == schedule_review(**inputs)
