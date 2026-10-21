"""The existing reconciliation savepoint, inside the command caller's transaction."""


def begin_reconciliation(database) -> None:
    database.raw.execute("SAVEPOINT reconcile_review")


def rollback_reconciliation(database) -> None:
    database.raw.execute("ROLLBACK TO SAVEPOINT reconcile_review")


def release_reconciliation(database) -> None:
    database.raw.execute("RELEASE SAVEPOINT reconcile_review")
