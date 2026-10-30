"""One requested collection-only page on the existing background worker."""
from datetime import datetime, timezone
import os

from app.games.feedback.review import ReviewUnavailable


def execute_refresh_slice(task):
    # Resolve concrete dependencies before opening any bounded database section.
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore
    from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore
    from tempo_legacy_database.adapters.postgres_store import connection
    from app.services.durable_tasks import (
        lock_current_slice, advance_task_slice_in_transaction, complete_task_slice_in_transaction)
    from app.services.redis_admission_gate import background_lease, BackgroundAdmissionDeferred
    from app.services.study_priority import control_snapshot
    from .game_review import SystemClock, configuration_policy
    from .game_sync import create_game_sync
    from .game_sync_current import CurrentRepertoireSnapshots

    policy = control_snapshot()
    if policy.get("available") is not True or policy.get("active") is not False or policy.get("study_only_mode") is not False:
        raise BackgroundAdmissionDeferred("Refresh is waiting for Study or background admission")
    with background_lease(), connection(read_only=False, background=True) as database:
        if not lock_current_slice(database, task):
            return False
    dsn = os.environ.get("TEMPO_DATABASE_WRITE_URL")
    if not dsn:
        raise ReviewUnavailable("games_writer_profile_required", 503)
    connections, clock = DedicatedGamesConnections(dsn), SystemClock()
    store = PostgresCanonicalGamesStore(connections)
    job = store.load_job(task["payload"]["job_id"])
    if job is None or job.spec.purpose != "refresh" or job.spec.workspace_id != task["payload"]["workspace_id"]:
        raise ValueError("exact_requested_refresh_required")
    if job.status not in {"complete", "failed", "quarantined"}:
        configuration = PostgresReviewStore(connections).configuration(job.spec.workspace_id, job.spec.provider)
        if configuration is None or configuration.revision != job.spec.configuration_revision:
            store.save_job(job.model_copy(update={"status": "failed", "lease_until": 0,
                "error": "Sync settings changed. Start a new refresh."}))
        else:
            runtime = create_game_sync(policies=(configuration_policy(configuration),),
                store=store, snapshots=CurrentRepertoireSnapshots(connections), clock=clock,
                learning=None, source_identity=store, encounter_feedback=True, delivery_enabled=False)
            try:
                runtime.runner.run_once(job_id=job.spec.job_id)
            finally:
                runtime.close()
    job = store.load_job(job.spec.job_id)
    with background_lease(), connection(read_only=False, background=True) as database:
        if not lock_current_slice(database, task):
            return False
        if job.status in {"complete", "failed", "quarantined"}:
            complete_task_slice_in_transaction(database, task)
            return False
        eligible = max(clock.now() + 1, job.next_attempt_at,
                       job.lease_until if job.status == "running" else 0)
        return advance_task_slice_in_transaction(database, task, next_phase="fetch",
            next_payload=task["payload"],
            eligible_at=datetime.fromtimestamp(eligible, timezone.utc).isoformat())
