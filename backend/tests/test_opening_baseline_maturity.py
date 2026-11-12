"""Normal review and replay present identical augmented facts to unlock."""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from app.study.policy import review_reconciliation as replay
from app.study.policy import scheduling_review_application as normal


@pytest.mark.parametrize(('ratings', 'seed'), [
    ([], None), ([], (3, 2)), (['again'], (2, 2)),
    (['correct'], (1, 1)), (['correct', 'again'], (4, 2)),
])
def test_normal_and_replay_augment_same_history(monkeypatch, ratings, seed):
    now = datetime(2026, 1, 3, tzinfo=timezone.utc)
    rows = [dict(id=i + 1, rating=rating, reviewed_at=f'2026-01-0{i+1}T00:00:00+00:00') for i, rating in enumerate(ratings)]
    days = sum(r == 'correct' for r in ratings)
    baseline = normal.OpeningSeedFacts(*seed) if seed else None
    facts = normal.MaturityFacts(days, tuple(reversed(ratings)), baseline)
    card = normal.CardState('2026-01-01', 0, None, None, False, 0, False, 'learning', 'normal', 0, '[]')
    class Store:
        def source_review_exists(self, *_): return False
        def load_card_state(self, *_, **__): return card
        def append_review(self, *_): pass
        def load_maturity_facts(self, *_): return facts
        def save_card_state(self, *_): pass
    seen = []
    def unlock(stability, successful_days, recent):
        seen.append((successful_days, list(recent)))
        return False
    normal.apply_scheduling_review(Store(), 'c', 'correct', guided=False, source_kind='study', source_ref=None,
        light_first_interval_days=7, reviewed_at=now, review_day=date(2026, 1, 3), unlock_ready_function=unlock)
    monkeypatch.setattr(replay.storage, 'light_interval_setting', lambda *_: (7,))
    monkeypatch.setattr(replay.storage, 'valid_review_history', lambda *_: rows)
    monkeypatch.setattr(replay.storage, 'baseline_schedule_seed', lambda *_: None if seed is None else dict(baseline_successful_days=seed[0], baseline_recent_clean=seed[1]))
    monkeypatch.setattr(replay, 'unlock_ready', unlock)
    state = dict(interval_days=0, fsrs_card_json=None, first_correct_at=None, reinforcement_pending=False,
        scheduling_mode='normal', hard_correct_streak=0, recent_attempts_json='[]')
    replay._next_state(None, 'c', state, 'correct', False, now, 99, 'UTC')
    assert seen[0] == seen[1]
    assert seen[0] == (days + (seed[0] if seed else 0), list(reversed(ratings)) + ['correct'] * min(seed[1] if seed else 0, 2 - len(ratings)))
