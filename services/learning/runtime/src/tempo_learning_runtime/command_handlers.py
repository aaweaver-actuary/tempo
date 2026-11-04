"""Learning command definitions and handler policies, independent of HTTP composition."""

from datetime import datetime, timezone
import uuid
from fastapi import HTTPException
from tempo_learning_database.adapters import api as storage
from . import gateway, queue_commands, study_attempt_commands
from . import study_bootstrap, teaching_commands, opening_evidence as evidence, publication_bridge
from .review_adapter import submit_legacy_review
from .review_conflicts import ReviewConflict
from .publications import activate, retire
from .tactic_commands import submit_tactic_attempt


def reconcile(database, payload):
    storage.begin_reconciliation(database)
    try:
        result = submit_legacy_review(database, payload)
    except ReviewConflict as error:
        storage.rollback_reconciliation(database)
        result = {"persisted": False, "conflict": error.information()}
    storage.release_reconciliation(database)
    return result


def save_endgame_attempt(db, payload):
    template = storage.active_endgame_template(db, payload["template_id"])
    if template is None:
        raise HTTPException(409, "Endgame template unavailable")
    card = storage.lock_endgame_card(db, template["card_id"])
    # Retirement acquires this same card lock before disabling the template.
    current = storage.endgame_enabled(db, payload["template_id"])
    if not card or not card[0] or not current[0]:
        raise HTTPException(409, "Endgame definition retired")
    attempt_id = uuid.uuid4().hex
    storage.insert_endgame_attempt(db, attempt_id, payload["template_id"], payload["fen"], payload["target"], datetime.now(timezone.utc).isoformat())
    return {
        "id": attempt_id,
        "fen": payload["fen"],
        "target": payload["target"],
        "moves": payload["moves"],
    }


def register_handlers():
    handlers = {
        "study.settings.update": study_bootstrap.update_settings,
        "cards.teaching.record": teaching_commands.record_teaching,
        "cards.review": submit_legacy_review,
        "cards.review.reconcile": reconcile,
        "tactics.attempt.submit": submit_tactic_attempt,
        "opening_evidence.checkpoint": lambda db, p: evidence.persist_checkpoint(db, p),
        "endgames.attempt.create": save_endgame_attempt,
        "publication.activate": activate,
        "publication.activate.v2": publication_bridge.activate,
        "publication.retire": retire,
    }
    return gateway.CommandRegistry((*queue_commands.handler_definitions(),
                                    *study_attempt_commands.handler_definitions(),
                                    *handlers.items()))


