"""One bounded next-opponent publication using existing durable task admission."""
from datetime import datetime, timezone
import json

from fastapi import HTTPException

from app.contracts.next_opponent import NextOpponentProfile, NextOpponentProfileResponse, SpeedWeight
from tempo_legacy_database.adapters.postgres_store import connection
from tempo_legacy_database.adapters.derived_publications import next_opponent as storage
from tempo_legacy_database.adapters.derived_publications.next_opponent import _GAME_FIELDS
from .durable_tasks import complete_task_slice_in_transaction, enqueue_task_in_transaction, lock_current_slice
from app.games.opponents.profile import METHOD_VERSION, SAMPLE_LIMIT, build_profile, utc_time
from .redis_admission_gate import background_lease

TASK_KIND = "next_opponent_profile"


def _current_utc_time() -> datetime:
    return datetime.now(timezone.utc)


def request_profile_refresh(database) -> bool:
    """O(1) intent only, called at sync/account/exclusion publication boundaries."""
    configured = storage.configured_account(database)
    account = configured["lichess_username"].strip().lower() if configured else ""
    if not account:
        return False
    storage.ensure_account(database, account)
    state = storage.locked_refresh_state(database, _current_utc_time(), account)
    if (state["published_generation"] == state["input_generation"]
            and state["published_method"] == METHOD_VERSION and not state["future_evidence_due"]):
        return False
    payload = {"account": account, "input_generation": state["input_generation"], "method_version": METHOD_VERSION}
    existing = storage.existing_refresh_task(database, TASK_KIND, account)
    if existing and existing["state"] in {"queued", "leased", "retrying"} and json.loads(existing["payload_json"]) == payload:
        return False
    enqueue_task_in_transaction(database, TASK_KIND, account, payload, priority=135)
    return True


def _load_inputs(account: str, as_of: datetime) -> tuple[int, list[dict], list[dict], str | None] | None:
    with background_lease():
        with connection(read_only=True, background=True) as database:
            state = storage.account_input_generation(database, account)
            if state is None:
                return None
            sample = storage.sample_games(database, account, as_of, SAMPLE_LIMIT + 1)
            latest_ratings = []
            for speed in ("blitz", "rapid", "classical"):
                rating = storage.latest_speed_rating(database, account, speed, as_of)
                if rating:
                    latest_ratings.append(dict(rating))
            next_evidence = storage.next_evidence(database, account, as_of)
            return (int(state["input_generation"]), [dict(row) for row in sample], latest_ratings,
                    next_evidence["next_evidence_at"] if next_evidence else None)


def _publish(database, task: dict, input_generation: int, profile: NextOpponentProfile,
             next_evidence_at: str | None) -> bool:
    # Same order as account commands: settings, account state, durable task.
    configured = storage.locked_configured_account(database)
    state = storage.locked_account_input_generation(database, profile.source_account)
    if not lock_current_slice(database, task):
        return False
    if (configured is None or configured["lichess_username"].strip().lower() != profile.source_account
            or state is None or state["input_generation"] != input_generation
            or input_generation != task["payload"]["input_generation"]
            or task["payload"]["method_version"] != METHOD_VERSION):
        return complete_task_slice_in_transaction(database, task)
    published_at = _current_utc_time().isoformat()
    storage.store_profile_snapshot(
        database, profile.version, profile.source_account, profile.method_version,
        profile.model_dump_json(), published_at,
    )
    storage.mark_profile_published(
        database, input_generation, METHOD_VERSION, profile.version, next_evidence_at, profile.source_account,
    )
    return complete_task_slice_in_transaction(database, task)


def execute_profile_slice(task: dict) -> bool:
    account = task["payload"]["account"]
    as_of = _current_utc_time()
    inputs = _load_inputs(account, as_of)
    if inputs is None:
        with background_lease():
            with connection(read_only=False, background=True) as database:
                if not lock_current_slice(database, task):
                    return False
                return complete_task_slice_in_transaction(database, task)
    input_generation, records, ratings, next_evidence_at = inputs
    # No database connection or admission lease is held during computation.
    profile = build_profile(account, records, as_of=as_of, rating_records=ratings)
    with background_lease():
        with connection(read_only=False, background=True) as database:
            return _publish(database, task, input_generation, profile, next_evidence_at)


def read_profile(database, speed: str = "auto", *, now: datetime | None = None) -> NextOpponentProfileResponse:
    now = now or _current_utc_time()
    configured = storage.configured_account(database)
    if configured is None:
        raise HTTPException(503, "Next-opponent profile settings are unavailable; check Tempo service status and database initialization.")
    account = configured["lichess_username"].strip().lower()
    if not account:
        return NextOpponentProfileResponse(availability="unknown", refresh_status="idle", stale=True,
            stale_reasons=("no_account",), requested_speed=speed, detail="Configure a Lichess account and sync games.")
    row = storage.published_profile_row(database, now, account)
    sync = storage.account_sync_state(database)
    task = storage.profile_task_state(database, TASK_KIND, account)
    same_sync = bool(sync and sync["username"].strip().lower() == account)
    sync_time = sync["last_success_at"] if same_sync else None
    last_success = utc_time(sync_time)
    pending = (row is None or row["published_generation"] != row["input_generation"]
               or row["published_method"] != METHOD_VERSION
               or row["future_evidence_due"])
    refresh_status = ("sync_error" if same_sync and sync["status"] == "error" else
                      "failed" if task and task["state"] == "failed" else
                      "pending" if pending else "idle")
    profile = NextOpponentProfile.model_validate_json(row["profile_json"]) if row and row["profile_json"] else None
    mixture = profile.speed_mixture if profile and speed == "auto" else (
        (SpeedWeight(speed=speed, weight=1),) if speed != "auto" else ())
    reasons = []
    if last_success is None:
        reasons.append("no_successful_sync")
    elif (now - last_success).total_seconds() > 7 * 86400:
        reasons.append("sync_older_than_seven_days")
    if pending:
        reasons.append("inputs_pending")
    if refresh_status in {"failed", "sync_error"}:
        reasons.append(refresh_status)
    stale_cohorts = []
    if profile:
        selected = {item.speed for item in mixture if item.weight > 0}
        for cohort in profile.cohorts:
            if cohort.speed not in selected:
                continue
            dates = [utc_time(cohort.latest_game_at), utc_time(cohort.rating_observed_at)]
            if any(value is None or (now - value).total_seconds() > 30 * 86400 for value in dates):
                stale_cohorts.append(cohort.speed)
        if stale_cohorts:
            reasons.append("cohort_evidence_older_than_thirty_days_or_missing")
    known = bool(profile and any(cohort.player_rating is not None and any(
        item.speed == cohort.speed and item.weight > 0 for item in mixture) for cohort in profile.cohorts))
    unsupported_only = bool(mixture and all(item.speed not in {"blitz", "rapid", "classical"} for item in mixture))
    return NextOpponentProfileResponse(
        availability="unsupported" if unsupported_only else "available" if known else "pending" if profile is None and pending else "unknown",
        refresh_status=refresh_status, stale=bool(reasons), stale_reasons=tuple(reasons),
        stale_cohorts=tuple(stale_cohorts), source_account=account, last_successful_sync_at=sync_time,
        published_at=row["published_at"] if row else None, requested_speed=speed,
        effective_speed_mixture=mixture, profile=profile,
        detail=None if profile else "Sync Lichess games to publish a next-opponent profile.",
    )
