"""Bounded, source-independent next-opponent estimator (not a calibrated model)."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json

from ..next_opponent_contract import NextOpponentProfile, OpponentCohort, RatingWeight, SpeedWeight

SUPPORTED_SPEEDS = ("blitz", "rapid", "classical")
METHOD_VERSION = "next-opponent-v1"
SAMPLE_LIMIT = 1000
WINDOW_DAYS = 90
HALF_LIFE_DAYS = 30
PRIOR_GAMES = 10
PRIOR_OFFSETS = ((-100, .25), (0, .5), (100, .25))
EVIDENCE_FIELDS = ("id", "played_at", "speed", "player_rating", "opponent_rating",
                   "rating_change", "time_control")


def utc_time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if value else None
        if parsed is None:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def semantic_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _valid_rating(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def build_profile(account: str, records: list[dict], *, as_of: datetime,
                  rating_records: list[dict] | None = None) -> NextOpponentProfile:
    """Pure chronological replay seam. No wall clock, provider or database calls.

    Persisted callers supply at most 1,001 sample rows plus three independently
    indexed latest-rating rows, so an infrequently played speed retains its own
    explicitly stale rating without an unbounded history scan.
    """
    normalized_account = account.strip().lower()
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)

    def eligible_rows(rows):
        normalized = []
        for record in rows:
            played_at = utc_time(record.get("played_at"))
            if (record.get("provider") != "lichess"
                    or str(record.get("username", "")).strip().lower() != normalized_account
                    or record.get("rated") != 1 or record.get("adaptive_excluded", 0)
                    or played_at is None or played_at > cutoff):
                continue
            normalized.append({**record, "played_at": played_at.isoformat()})
        return sorted(normalized, key=lambda row: (row["played_at"], row["id"]), reverse=True)

    eligible = eligible_rows(records)
    rating_candidates = eligible_rows(rating_records) if rating_records is not None else eligible
    latest_ratings = {
        speed: next((row for row in rating_candidates
                     if row["speed"] == speed and _valid_rating(row.get("player_rating"))), None)
        for speed in SUPPORTED_SPEEDS
    }
    anchor = utc_time(eligible[0]["played_at"]) if eligible else None
    earliest_datetime = datetime.min.replace(tzinfo=timezone.utc)
    window_start = (max(anchor, earliest_datetime + timedelta(days=WINDOW_DAYS))
                    - timedelta(days=WINDOW_DAYS)) if anchor else earliest_datetime
    recent = [row for row in eligible if utc_time(row["played_at"]) >= window_start]
    truncated = len(recent) > SAMPLE_LIMIT
    recent = recent[:SAMPLE_LIMIT]
    quality_flags = []
    if truncated:
        quality_flags.append("sample_truncated")
    if not recent:
        quality_flags.append("no_rated_games")
    weighted_games = [(row, 2 ** (-(anchor - utc_time(row["played_at"])).total_seconds()
                                    / (86400 * HALF_LIFE_DAYS))) for row in recent]
    speed_counts = defaultdict(float)
    for row, weight in weighted_games:
        speed_counts[row["speed"]] += weight
    total_speed_weight = sum(speed_counts.values())
    mixture = tuple(SpeedWeight(speed=speed, weight=weight / total_speed_weight)
                    for speed, weight in sorted(speed_counts.items())) if total_speed_weight else tuple(
        SpeedWeight(speed=speed, weight=1 / 3) for speed in SUPPORTED_SPEEDS)
    if not total_speed_weight:
        quality_flags.append("speed_mixture_fallback")
    unsupported_mass = sum(item.weight for item in mixture if item.speed not in SUPPORTED_SPEEDS)
    if unsupported_mass:
        quality_flags.append("unsupported_speed")
    cohorts = []
    for speed in SUPPORTED_SPEEDS:
        speed_games = [(row, weight) for row, weight in weighted_games if row["speed"] == speed]
        pairs = [(row, weight) for row, weight in speed_games
                 if _valid_rating(row.get("player_rating")) and _valid_rating(row.get("opponent_rating"))]
        pair_mass = sum(weight for _, weight in pairs)
        squared_mass = sum(weight * weight for _, weight in pairs)
        effective_sample = pair_mass * pair_mass / squared_mass if squared_mass else 0
        rating_row = latest_ratings[speed]
        player_rating = rating_row["player_rating"] if rating_row else None
        cohort_flags = []
        if rating_row:
            rating_change = rating_row.get("rating_change")
            if isinstance(rating_change, int) and not isinstance(rating_change, bool):
                player_rating += rating_change
            else:
                cohort_flags.append("missing_rating_change")
            if player_rating <= 0:
                player_rating = None
                cohort_flags.append("invalid_postgame_rating")
        if player_rating is None:
            cohort_flags.append("missing_player_rating")
        if len(pairs) != len(speed_games):
            cohort_flags.append("missing_rating_pairs")
        if effective_sample < PRIOR_GAMES:
            cohort_flags.append("sparse_sample")
        if not pairs:
            cohort_flags.append("cold_start_prior")
        cohort_flags.append("uncalibrated_prior")
        probability_mass = defaultdict(float)
        if player_rating is not None:
            for offset, prior_weight in PRIOR_OFFSETS:
                probability_mass[player_rating + offset] += PRIOR_GAMES * prior_weight
            for row, weight in pairs:
                probability_mass[player_rating + row["opponent_rating"] - row["player_rating"]] += weight
        denominator = PRIOR_GAMES + pair_mass
        cohorts.append(OpponentCohort(
            speed=speed, player_rating=player_rating,
            rating_observed_at=rating_row["played_at"] if rating_row else None,
            latest_game_at=speed_games[0][0]["played_at"] if speed_games else None,
            game_count=len(speed_games), rating_pair_count=len(pairs),
            effective_sample_size=effective_sample, prior_weight=PRIOR_GAMES / denominator,
            opponent_ratings=tuple(RatingWeight(rating=rating, weight=mass / denominator)
                                   for rating, mass in sorted(probability_mass.items())),
            quality_flags=tuple(sorted(cohort_flags)),
        ))
    evidence = {
        "games": [{field: row.get(field) for field in EVIDENCE_FIELDS} for row in recent],
        "latest_ratings": {speed: ({field: row.get(field) for field in EVIDENCE_FIELDS} if row else None)
                           for speed, row in latest_ratings.items()},
        "truncated": truncated,
    }
    evidence_digest = semantic_digest(evidence)
    model = dict(method_version=METHOD_VERSION, source_account=normalized_account,
                 evidence_digest=evidence_digest, evidence_watermark=anchor.isoformat() if anchor else None,
                 game_count=len(recent), speed_mixture=mixture, unsupported_speed_mass=unsupported_mass,
                 cohorts=tuple(cohorts), quality_flags=tuple(sorted(quality_flags)))
    version = semantic_digest({"configuration": [METHOD_VERSION, WINDOW_DAYS, HALF_LIFE_DAYS,
                                               PRIOR_GAMES, PRIOR_OFFSETS, SAMPLE_LIMIT],
                               "account": normalized_account, "evidence": evidence_digest})
    return NextOpponentProfile(version=version, **model)
