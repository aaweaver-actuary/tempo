"""Borrowed-transaction admission of a durable coverage seed request."""
from datetime import datetime, timezone
import json
import uuid

from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.coverage import seeding as storage
from .durable_tasks import enqueue_task_in_transaction
from .repertoire_coverage import recent_player_cohort
from .canonical_scope_freshness import coverage_run_is_current, scope_identity
from .canonical_prefix import read_prefix


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_fingerprint(database: PostgresConnection, repertoire_id: str) -> str:
    row = storage.source_hash(database, (repertoire_id,))
    prefix = read_prefix(database, repertoire_id)
    scope_fingerprint = f":{prefix['source_revision']}:{prefix['preview_id']}" if prefix['moves'] else ''
    return f"{row[0]}:{prefix['revision']}{scope_fingerprint}"


def request_coverage_seed_in_transaction(
    database: PostgresConnection, repertoire_id: str, *, automatic: bool = False,
    supersede_active: bool = False,
) -> dict[str, str]:
    """Admit one coverage build and its durable task without traversing lines."""

    storage.lock_coverage(database, repertoire_id)
    exists = storage.repertoire_present(database, (repertoire_id,))
    if exists is None:
        raise KeyError("Repertoire not found")
    active = storage.active_run(database, (repertoire_id,))
    if active and not supersede_active and coverage_run_is_current(database, active, repertoire_id):
        return {"run_id": str(active["id"]), "status": "queued"}
    if active:
        storage.fail_run(database, ("Repertoire lines changed; a new coverage run was queued", _now(), active["id"]))
    settings = dict(storage.settings_row(database))
    cohort = recent_player_cohort(database, int(settings["coverage_maia_elo"]))
    settings_payload = {
        "automatic_priority": automatic,
        **scope_identity(database, repertoire_id, lock=True),
        "reply_denominator": settings["coverage_reply_denominator"],
        "cumulative_target": settings["coverage_cumulative_target"] / 100,
        "horizon_fullmoves": settings["coverage_horizon_fullmoves"],
        "path_floor": settings["coverage_path_floor"],
        "maia_elo": cohort["maia_elo"],
        "explorer_rating": cohort["explorer_rating"],
        "recent_median_rating": cohort["recent_median_rating"],
        "speed_weights": cohort["speed_weights"],
        "cohort_games": cohort["games"],
    }
    run_id = str(uuid.uuid4())
    now = _now()
    source_fingerprint = _source_fingerprint(database, repertoire_id)
    storage.insert_run(database, (run_id, repertoire_id, json.dumps(settings_payload), now, now))
    enqueue_task_in_transaction(
        database, "coverage_seed", repertoire_id,
        {"run_id": run_id, "repertoire_id": repertoire_id,
         "horizon_fullmoves": int(settings_payload["horizon_fullmoves"]),
         "source_fingerprint": source_fingerprint,
         "after_line_id": "", "position_index": 0},
        priority=75,
    )
    return {"run_id": run_id, "status": "queued"}

