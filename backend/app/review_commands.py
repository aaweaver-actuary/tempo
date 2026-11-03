"""Legacy command assembly: explicit Learning registration and owned savepoint."""
from tempo_learning_review import register_review_commands
from .command_gateway import register_command
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.controls import review_reconciliation as storage
from .services.learning_review_adapter import submit_legacy_review
from typing import Any


def submit_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_legacy_review(database, payload)


def reconcile_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    from app.contracts.review_conflicts import ReviewConflict

    # A handled conflict becomes a successful transport receipt, so the gateway
    # cannot roll back its handler savepoint for us. Fence the entire ordinary
    # review path, including evidence writes, before converting that exception.
    storage.begin_reconciliation(database)
    try:
        result = submit_review(database, payload)
    except ReviewConflict as error:
        storage.rollback_reconciliation(database)
        result = {"persisted": False, "conflict": error.information()}
    storage.release_reconciliation(database)
    return result


def prepare_review(payload):
    """Prepare both review inputs before the command gateway opens its writer."""
    from tempo_legacy_database.adapters.study.recombination import prepare_recombination_review
    prepared = prepare_recombination_review(payload)
    request = prepared['review'].get('opening_evidence_completion')
    if request:
        from app.contracts.transport.study import OpeningEvidenceCheckpoint
        from .services.postgres_opening_evidence import prepare_position_checkpoint
        from tempo_legacy_database.connections import read_connection
        with read_connection() as database:
            prepared['prepared_position_checkpoint'] = prepare_position_checkpoint(
                database, OpeningEvidenceCheckpoint.model_validate(request))
    return prepared


def register_prepared_review(name, handler):
    register_command(name, handler, prepare=prepare_review)


register_review_commands(register_prepared_review, submit=submit_review, reconcile=reconcile_review)
