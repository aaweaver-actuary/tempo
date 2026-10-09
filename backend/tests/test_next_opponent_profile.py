"""Issue #107: source-backed, deterministic next-opponent estimates."""
from datetime import datetime, timezone

import pytest

from app.services.next_opponent_profile import build_profile

CUTOFF = datetime(2026, 10, 8, tzinfo=timezone.utc)


def game(identifier="g", speed="rapid", player=1450, opponent=1500, **changes):
    return {"id": identifier, "provider": "lichess", "username": "Alice", "rated": 1,
            "adaptive_excluded": 0, "played_at": "2026-10-07T12:00:00+00:00",
            "speed": speed, "player_rating": player, "opponent_rating": opponent,
            "rating_change": 0, "time_control": "600+5", **changes}


def test_issue107_rating_change_translates_opponent_distribution_without_source_requests():
    first = build_profile("alice", [game()], as_of=CUTOFF)
    second = build_profile("alice", [game(rating_change=100)], as_of=CUTOFF)
    before, after = first.cohorts[1], second.cohorts[1]
    assert after.player_rating == before.player_rating + 100
    assert [(point.rating - 100, point.weight) for point in after.opponent_ratings] == [
        (point.rating, point.weight) for point in before.opponent_ratings]
    assert second.version != first.version


def test_issue107_separate_speed_cohorts_sparse_prior_and_unsupported_mass():
    profile = build_profile("Alice", [game(), game("b", "blitz", 1200, 1100),
                                       game("u", "bullet", 900, 1000)], as_of=CUTOFF)
    assert {item.speed: item.weight for item in profile.speed_mixture} == pytest.approx(
        {"blitz": 1 / 3, "rapid": 1 / 3, "bullet": 1 / 3})
    assert profile.cohorts[0].player_rating == 1200
    assert profile.cohorts[1].player_rating == 1450
    assert "sparse_sample" in profile.cohorts[0].quality_flags
    assert profile.cohorts[2].player_rating is None
    assert profile.cohorts[2].opponent_ratings == ()
    assert "unsupported_speed" in profile.quality_flags
    assert profile.unsupported_speed_mass == pytest.approx(1 / 3)
    assert profile.cohorts[1].prior_weight == pytest.approx(10 / 11)


def test_issue107_profile_retry_order_and_clock_do_not_change_semantic_version():
    records = [game(), game("b", "blitz", 1200, 1100)]
    original = build_profile("Alice", records, as_of=CUTOFF)
    retry = build_profile("ALICE", list(reversed(records)), as_of=CUTOFF.replace(day=9))
    assert original == retry


def test_issue107_historical_cutoff_excludes_future_other_accounts_providers_and_casual_games():
    valid = game()
    contamination = [game("future", played_at="2026-10-09T00:00:00Z"),
                     game("other", username="Bob"), game("chesscom", provider="chess.com"),
                     game("casual", rated=0), game("excluded", adaptive_excluded=1)]
    assert build_profile("alice", [valid, *contamination], as_of=CUTOFF) == build_profile(
        "alice", [valid], as_of=CUTOFF)


def test_issue107_missing_pair_uses_labeled_prior_and_missing_player_stays_unknown():
    cohort = build_profile("alice", [game(opponent=None, rating_change=None)], as_of=CUTOFF).cohorts[1]
    assert [(item.rating, item.weight) for item in cohort.opponent_ratings] == [
        (1350, .25), (1450, .5), (1550, .25)]
    assert {"missing_rating_change", "cold_start_prior"} <= set(cohort.quality_flags)
    unknown = build_profile("alice", [game(player=None)], as_of=CUTOFF).cohorts[1]
    assert unknown.player_rating is None and unknown.opponent_ratings == ()


def test_issue107_recent_weights_bound_sample_and_exclude_old_evidence():
    records = [game(str(index), played_at="2026-10-07T12:00:00Z") for index in range(1001)]
    records.append(game("old", speed="blitz", played_at="2026-01-01T00:00:00Z"))
    profile = build_profile("alice", records, as_of=CUTOFF)
    assert profile.game_count == 1000 and "sample_truncated" in profile.quality_flags
    assert profile.cohorts[0].game_count == 0
    assert profile.cohorts[0].player_rating == 1450
    assert profile.cohorts[0].rating_observed_at == "2026-01-01T00:00:00+00:00"
    assert profile.cohorts[1].effective_sample_size == pytest.approx(1000)


def test_issue107_offsets_use_historical_pregame_rating_and_recency_weights():
    profile = build_profile("alice", [game(rating_change=100),
        game("older", player=1000, opponent=1200, played_at="2026-09-07T12:00:00Z")], as_of=CUTOFF)
    cohort = profile.cohorts[1]
    assert cohort.player_rating == 1550
    points = {point.rating: point.weight for point in cohort.opponent_ratings}
    assert points[1600] == pytest.approx(1 / 11.5)
    assert points[1750] == pytest.approx(.5 / 11.5)
    assert sum(points.values()) == pytest.approx(1)


def test_issue107_timezones_invalid_dates_and_invalid_ratings_are_explicit():
    profile = build_profile("alice", [game(played_at="2026-10-07T08:00:00-04:00"),
        game("invalid", played_at="invalid"), game("negative", speed="blitz", player=-1)], as_of=CUTOFF)
    assert profile.evidence_watermark == "2026-10-07T12:00:00+00:00"
    assert profile.cohorts[0].player_rating is None
    assert "missing_player_rating" in profile.cohorts[0].quality_flags


def test_issue107_naive_historical_cutoff_is_utc_in_every_host_timezone():
    """Separate processes exercise real TZ handling without leaking global clocks."""
    import json
    import os
    import subprocess
    import sys
    script = """import json,sys
from datetime import datetime
from app.services.next_opponent_profile import build_profile
profile=build_profile('alice',json.loads(sys.argv[1]),as_of=datetime(2026,10,8))
print(profile.model_dump_json())
"""
    future_record = game(played_at="2026-10-08T02:00:00Z")
    profiles = [json.loads(subprocess.check_output(
        [sys.executable, "-c", script, json.dumps([future_record])],
        env={**os.environ, "TZ": zone}, text=True, timeout=10,
    )) for zone in ("UTC", "America/New_York")]
    assert [profile["game_count"] for profile in profiles] == [0, 0]
    assert profiles[0] == profiles[1]
