"""Study-owned durable sweeps, independent of optional producer execution."""
from datetime import datetime, timezone
import json

from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_learning_remediation import RemediationError
from tempo_legacy_database.connections import background_read_connection, connection
from tempo_legacy_database.adapters.durable_tasks import storage as task_storage
from tempo_legacy_database.adapters.games.authority import acceptance as source_owner
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore, installed
from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentDecisionUnavailable, current_owner_game_feedback

from app.contracts.product_v02.learning import GameEffectControl
from app.legacy_game_observation import capture_game
from app.repertoire import project_capture
from app.services.durable_tasks import (advance_task_slice_in_transaction, claim_task,
    complete_task_slice_in_transaction, enqueue_task_in_transaction, lock_current_slice)
from app.study.game_feedback import GameFeedback, effect_id

KIND = "study_game_feedback"


def claim_feedback_sweep():
    with background_read_connection() as database:
        if not installed(database) or not PostgresGameFeedbackStore(database).owed_page("", 1):
            return None
    with connection(background=True) as database:
        task = task_storage.task_by_kind(database, (KIND, "current"))
        if task is None or task["state"] not in {"queued", "leased", "retrying"}:
            enqueue_task_in_transaction(database, KIND, "current", {"after": ""}, priority=15)
    return claim_task(KIND)


def execute_feedback_sweep(task, *, read=background_read_connection, write=None,
                           capture=capture_game, clock=None):
    """One effect and durable cursor per transaction; all reads close first."""
    write = write or (lambda: connection(background=True))
    clock = clock or (lambda: datetime.now(timezone.utc))
    after = json.loads(task["payload_json"]).get("after", "")
    with read() as database:
        page = PostgresGameFeedbackStore(database).owed_page(after, 1)
    observed = page[0] if page else None
    captured = snapshot = None
    reason = None
    if observed is not None and observed.status == "pending":
        try:
            captured = capture(observed.lapse.finding.repertoire_id, observed.key.game_id,
                               (observed.key.occurrence_ply-1,))
            snapshot = project_capture(captured)
        except (CurrentDecisionUnavailable, RemediationError, ValueError) as error:
            # Keep the obligation. The durable sweep exposes this hold reason
            # and proceeds fairly; its next generation retries missing facts.
            reason = getattr(error, "reason", None) or "current_source_unavailable"
    with write() as database:
        if not lock_current_slice(database, task):
            return False
        if observed is None:
            complete_task_slice_in_transaction(database, task)
            return True
        if captured is not None and snapshot is not None:
            raw = captured["game"]
            source_owner.lock_imported_source(database, raw["provider"], raw["provider_game_id"])
            current = source_owner.imported_game(database, raw["id"])
            if current is None or dict(current) != raw:
                raise CurrentDecisionUnavailable("canonical_imported_source_changed")
            if observed.lapse.finding.source_digest != fingerprint(raw):
                # Changed source cannot newly apply the obsolete deferred lapse.
                GameFeedback(PostgresGameFeedbackStore(database), None, clock=clock).control_effect(
                    GameEffectControl(key=observed.key, source_revision=observed.source_revision,
                                      action="hold", reason="source_corrected"))
            else:
                current_owner_game_feedback(database, retained_owner_capture=captured,
                    snapshot=snapshot, clock=clock).maintain(after=after, limit=1)
        advance_task_slice_in_transaction(database, task, next_phase="scan",
            next_payload={"after": effect_id(observed.key), "hold_reason": reason})
    return False
