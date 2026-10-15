"""Legacy command assembly: explicit Learning registration and owned savepoint."""
from tempo_learning_review import register_review_commands
from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .services.learning_review_adapter import submit_legacy_review
from typing import Any


def submit_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_legacy_review(database, payload)


def reconcile_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    from .review_conflicts import ReviewConflict

    # A handled conflict becomes a successful transport receipt, so the gateway
    # cannot roll back its handler savepoint for us. Fence the entire ordinary
    # review path, including evidence writes, before converting that exception.
    database.raw.execute("SAVEPOINT reconcile_review")
    try:
        result = submit_review(database, payload)
    except ReviewConflict as error:
        database.raw.execute("ROLLBACK TO SAVEPOINT reconcile_review")
        result = {"persisted": False, "conflict": error.information()}
    database.raw.execute("RELEASE SAVEPOINT reconcile_review")
    return result


register_review_commands(register_command, submit=submit_review, reconcile=reconcile_review)
