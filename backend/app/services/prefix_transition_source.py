"""Bounded transition source capture and cooperative calculation."""

from tempo_legacy_database.adapters.prefix_reads import transitions as storage
from . import prefix_evaluation_source as structural
from .prefix_evaluation import PrefixEvaluationError
from .prefix_transition import SnapshotTable, TransitionSnapshot, canonical_json

MAX_TRANSITION_ROWS = 40_000
MAX_TRANSITION_BYTES = 4 * 1024 * 1024


def discover_memberships(identifier, deadline):
    structural.check_available(deadline)
    with storage.membership_connection() as database:
        rows = storage.membership_rows(database, identifier, MAX_TRANSITION_ROWS + 1)
    if len(rows) > MAX_TRANSITION_ROWS:
        raise PrefixEvaluationError(
            "limit_exceeded", "Too many memberships for a bounded transition plan."
        )
    structural.check_available(deadline)
    return tuple(row["card_id"] for row in rows)


def load_transition_snapshot(
    identifier, lookup_card_ids, study_day, deadline, *, reads=None
):
    """All transition inputs and structure share one read-only MVCC snapshot."""
    lookup_card_ids = tuple(sorted(set(lookup_card_ids)))

    def capture(database):
        return storage.capture_transition_tables(
            database,
            identifier,
            lookup_card_ids,
            MAX_TRANSITION_ROWS,
            MAX_TRANSITION_BYTES,
            PrefixEvaluationError,
        )

    source, raw_tables = structural.load_snapshot(
        identifier,
        deadline,
        capture=capture,
        **({"reads": reads} if reads is not None else {}),
    )
    tables = []
    for name, rows in raw_tables:
        tables.append(
            SnapshotTable(name, tuple(canonical_json(dict(row)) for row in rows))
        )
        structural.check_available(deadline)
    return TransitionSnapshot(source, study_day, tuple(tables), lookup_card_ids)


def calculate(generator, deadline):
    try:
        while True:
            structural.check_available(deadline)
            try:
                next(generator)
            except StopIteration as completed:
                return completed.value
    finally:
        generator.close()
