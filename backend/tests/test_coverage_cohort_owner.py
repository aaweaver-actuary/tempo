"""Coverage cohort selection uses the borrowed read and stable source policy."""
from datetime import datetime, timezone

import pytest

from app.services import coverage_cohort as owner


@pytest.mark.parametrize(('rows', 'fallback', 'expected'), [
    ([], 1150, (1150, 1200, 1100, {'blitz': 1/3, 'rapid': 1/3, 'classical': 1/3}, 0)),
    ([{'player_rating': 1000, 'speed': 'blitz'}, {'player_rating': 1200, 'speed': 'rapid'}], 1900,
     (1100, 1000, 1100, {'blitz': .5, 'rapid': .5}, 2)),
    ([{'player_rating': None, 'speed': 'unknown'}, {'player_rating': 2500, 'speed': 'classical'}], 1100,
     (2500, 2500, 1900, {'classical': 1.0}, 2)),
])
def test_cohort_uses_borrowed_read_and_preserves_policy(monkeypatch, rows, fallback, expected):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(owner, 'datetime', Clock)
    database = object()
    seen = []
    monkeypatch.setattr(owner.storage, 'recent_cohort_games', lambda db, params: seen.append((db, params)) or rows)
    result = owner.recent_player_cohort(database, fallback)
    assert seen == [(database, ('2026-07-03T12:00:00+00:00',))]
    assert tuple(result[key] for key in ('recent_median_rating', 'explorer_rating', 'maia_elo', 'speed_weights', 'games')) == expected


def test_legacy_cohort_exports_are_identical():
    from app.services import repertoire_coverage as legacy
    for name in ('SUPPORTED_RATING_BUCKETS', 'SUPPORTED_MAIA_ELOS', '_nearest_rating_bucket', 'recent_player_cohort'):
        assert getattr(legacy, name) is getattr(owner, name)


def test_legacy_probability_exports_are_identical():
    from app.services import coverage_probabilities, repertoire_coverage
    for name in ('CoverageCandidate', 'blend_probabilities', 'required_reply_moves'):
        assert getattr(repertoire_coverage, name) is getattr(coverage_probabilities, name)
