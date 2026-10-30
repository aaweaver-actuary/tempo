"""Pure catch-up window policy and exact requested-job projection."""
from datetime import datetime, timezone
import hashlib

from app.contracts.product_v02.games import GameRefreshStatus, GameWindow

TASK_KIND = "product_game_refresh"
LOOKBACK_SECONDS = 2 * 86400  # Same safety lookback as the existing incremental sync.


def refresh_job(configuration, checkpoint, now, operation_id, build_job):
    start = datetime.fromisoformat(configuration.window.start_at.replace("Z", "+00:00")).timestamp()
    if checkpoint is not None:
        covered = datetime.fromisoformat(checkpoint.spec.window.end_at.replace("Z", "+00:00")).timestamp()
        start = max(start, covered - LOOKBACK_SECONDS)
    end = int(now * 1000) / 1000
    if end <= start:
        raise ValueError("Refresh needs a configured start date before the current time.")
    window = GameWindow(start_at=datetime.fromtimestamp(start, timezone.utc).isoformat(),
        end_at=datetime.fromtimestamp(end, timezone.utc).isoformat(), precision="millisecond")
    identity = hashlib.sha256(f"{configuration.workspace_id}\0{operation_id}".encode()).hexdigest()
    job_id = f"refresh:{configuration.provider}:{configuration.revision}:{identity}"
    return build_job(configuration.model_copy(update={"window": window}), job_id).model_copy(
        update={"purpose": "refresh", "configuration_revision": configuration.revision})


def refresh_status(operation_id, job):
    return GameRefreshStatus(provider=job.spec.provider, operation_id=operation_id,
        job_id=job.spec.job_id, window=job.spec.window, status=job.status,
        pages_committed=job.pages_committed, next_attempt_at=job.next_attempt_at, error=job.error)
