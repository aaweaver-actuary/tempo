"""Borrowed-read cohort facts for coverage selection."""

from datetime import datetime, timedelta, timezone
from statistics import median

from tempo_legacy_database.adapters.coverage import repertoire as storage


SUPPORTED_RATING_BUCKETS = (1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500)
SUPPORTED_MAIA_ELOS = (1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900)


def _nearest_rating_bucket(rating: int) -> int:
    return min(
        SUPPORTED_RATING_BUCKETS,
        key=lambda candidate: (abs(candidate - rating), candidate),
    )


def recent_player_cohort(database, fallback_rating: int) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    rows = storage.recent_cohort_games(database, (cutoff,))
    ratings = [
        int(row["player_rating"]) for row in rows if row["player_rating"] is not None
    ]
    representative_rating = round(median(ratings)) if ratings else fallback_rating
    speed_counts = {
        speed: sum(row["speed"] == speed for row in rows)
        for speed in ("blitz", "rapid", "classical")
    }
    speed_total = sum(speed_counts.values())
    speed_weights = (
        {speed: count / speed_total for speed, count in speed_counts.items() if count}
        if speed_total
        else {"blitz": 1 / 3, "rapid": 1 / 3, "classical": 1 / 3}
    )
    return {
        "recent_median_rating": representative_rating,
        "explorer_rating": _nearest_rating_bucket(representative_rating),
        "maia_elo": min(
            SUPPORTED_MAIA_ELOS,
            key=lambda candidate: (abs(candidate - representative_rating), candidate),
        ),
        "speed_weights": speed_weights,
        "games": len(rows),
    }
