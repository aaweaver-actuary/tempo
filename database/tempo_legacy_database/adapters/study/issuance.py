"""Actual Study head-issuance facts on the existing durable attempt origins.

These functions borrow the caller's transaction. They neither admit work nor
infer authority for historical rows whose first issuance was not recorded.
"""
from datetime import datetime, timezone

from tempo_legacy_database.adapters.postgres_store import PostgresConnection


def issuance_frontier(database, *, lock=False) -> int:
    """Capture a committed generation fence after card/day locks when writing.

    The same transaction lock is taken by first issuance. A reset/admission
    cannot observe an intermediate generation that later becomes an old issued
    attempt. Sequence gaps from rolled-back transactions never count as cards.
    """
    if lock and isinstance(database, PostgresConnection):
        database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                ("tempo:study-attempt-issuance",))
    return int(database.execute(
        "SELECT COALESCE(MAX(issuance_sequence),0) FROM queue_attempt_origins"
    ).fetchone()[0])


def stamp_issued_head(database, entry_id, card_id, revision, *, now=None):
    """Stamp only a newly authorized attempt; reload preserves its generation."""
    issuance_frontier(database, lock=True)
    observed = now or datetime.now(timezone.utc)
    if isinstance(database, PostgresConnection):
        return database.execute_native("""UPDATE queue_attempt_origins SET
            issuance_sequence=CASE WHEN issued_as_head=0
                THEN nextval('study_attempt_issuance_sequence') ELSE issuance_sequence END,
            issued_at=CASE WHEN issued_as_head=0 THEN %s ELSE issued_at END,
            issued_as_head=1
            WHERE queue_entry_id=%s AND card_id=%s AND revision=%s""",
            (observed, entry_id, card_id, revision)).rowcount
    # SQLite's command writer already serializes writes. No second receipt or
    # process-local counter is introduced for its recovery adapter.
    return database.execute("""UPDATE queue_attempt_origins SET
        issuance_sequence=CASE WHEN issued_as_head=0
            THEN (SELECT COALESCE(MAX(issuance_sequence),0)+1 FROM queue_attempt_origins)
            ELSE issuance_sequence END,
        issued_at=CASE WHEN issued_as_head=0 THEN ? ELSE issued_at END,
        issued_as_head=1
        WHERE queue_entry_id=? AND card_id=? AND revision=?""",
        (observed.isoformat(), entry_id, card_id, revision)).rowcount


def _evidence(row):
    if row is None or row["issuance_sequence"] is None or row["issued_at"] is None:
        return None
    from app.contracts.product_v02.learning import IssuedAttemptEvidence
    issued_at = row["issued_at"]
    if isinstance(issued_at, datetime):
        issued_at = issued_at.astimezone(timezone.utc).isoformat()
    return IssuedAttemptEvidence(queue_entry_id=row["queue_entry_id"], card_id=row["card_id"],
        card_revision=row["revision"], issuance_sequence=row["issuance_sequence"], issued_at=issued_at)


def attempt_issuance(database, queue_entry_id, card_id, revision):
    row = database.execute("""SELECT queue_entry_id,card_id,revision,issuance_sequence,issued_at
        FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=? AND revision=?
          AND issued_as_head=1""", (queue_entry_id, card_id, revision)).fetchone()
    return _evidence(row)


def completion_issuance(database, review_id, card_id, revision):
    """Resolve a real saved review through its retained same-attempt receipt."""
    rows = database.execute("""SELECT DISTINCT origin.queue_entry_id,origin.card_id,
            origin.revision,origin.issuance_sequence,origin.issued_at
        FROM reviews review JOIN review_attempt_receipts receipt ON receipt.review_id=review.id
        JOIN queue_attempt_origins origin ON origin.queue_entry_id=receipt.queue_entry_id
          AND origin.card_id=receipt.card_id AND origin.card_id=review.card_id
        WHERE review.id=? AND origin.card_id=? AND origin.revision=?
          AND origin.issued_as_head=1 AND review.source_kind='study'
          AND review.invalidated_at IS NULL LIMIT 2""", (review_id, card_id, revision)).fetchall()
    return _evidence(rows[0]) if len(rows) == 1 else None


def first_target_issuance(database, card_id, revision, determined_at, *, after_issuance_sequence=0):
    """Return actual target authority and its card ordinal after determination.

    Count committed distinct origins rather than sequence subtraction, queue
    positions or client sessions. An already-issued current attempt is excluded.
    """
    if not isinstance(database, PostgresConnection):
        determined_at = datetime.fromisoformat(determined_at.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
    row = database.execute("""SELECT queue_entry_id,card_id,revision,issuance_sequence,issued_at
        FROM queue_attempt_origins WHERE card_id=? AND revision=? AND issued_as_head=1
          AND issued_at>? AND issuance_sequence>? ORDER BY issuance_sequence LIMIT 1""",
        (card_id, revision, determined_at, after_issuance_sequence)).fetchone()
    evidence = _evidence(row)
    if evidence is None:
        return None
    ordinal = database.execute("""SELECT COUNT(*) FROM queue_attempt_origins
        WHERE issued_as_head=1 AND issued_at>? AND issuance_sequence<=?""",
        (determined_at, evidence.issuance_sequence)).fetchone()[0]
    return evidence, int(ordinal)
