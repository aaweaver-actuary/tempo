"""Learning command definitions and handler policies, independent of HTTP composition."""

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


def register_handlers():
    handlers = {
        "study.settings.update": study_bootstrap.update_settings,
        "cards.teaching.record": teaching_commands.record_teaching,
        "cards.review": submit_legacy_review,
        "cards.review.reconcile": reconcile,
        "tactics.attempt.submit": submit_tactic_attempt,
        "opening_evidence.checkpoint": lambda db, p: evidence.persist_checkpoint(db, p),
        "publication.activate": activate,
        "publication.activate.v2": publication_bridge.activate,
        "publication.retire": retire,
    }
    return gateway.CommandRegistry((*queue_commands.handler_definitions(),
                                    *study_attempt_commands.handler_definitions(),
                                    *handlers.items()))
