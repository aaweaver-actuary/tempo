"""Actual Study head-issuance facts on the existing durable attempt origins.

These functions borrow the caller's transaction. They neither admit work nor
infer authority for historical rows whose first issuance was not recorded.
"""
from datetime import datetime, timezone

def issuance_frontier(database, *, lock=False) -> int:
    """Capture a committed generation fence after card/day locks when writing.

    The same transaction lock is taken by first issuance. A reset/admission
    cannot observe an intermediate generation that later becomes an old issued
    attempt. Sequence gaps from rolled-back transactions never count as cards.
    """
    if lock:
        database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:study-attempt-issuance',))
    return int(database.execute('SELECT COALESCE(MAX(issuance_sequence),0) FROM queue_attempt_origins').fetchone()[0])

def stamp_issued_head(database, entry_id, card_id, revision, *, now=None):
    """Stamp only a newly authorized attempt; reload preserves its generation."""
    issuance_frontier(database, lock=True)
    observed = now or datetime.now(timezone.utc)
    return database.execute_native("UPDATE queue_attempt_origins SET\n            issuance_sequence=CASE WHEN issued_as_head=0\n                THEN nextval('study_attempt_issuance_sequence') ELSE issuance_sequence END,\n            issued_at=CASE WHEN issued_as_head=0 THEN %s ELSE issued_at END,\n            issued_as_head=1\n            WHERE queue_entry_id=%s AND card_id=%s AND revision=%s", (observed, entry_id, card_id, revision)).rowcount

def _evidence(row):
    if row is None or row['issuance_sequence'] is None or row['issued_at'] is None:
        return None
    from app.contracts.product_v02.learning import IssuedAttemptEvidence
    issued_at = row['issued_at']
    if isinstance(issued_at, datetime):
        issued_at = issued_at.astimezone(timezone.utc).isoformat()
    return IssuedAttemptEvidence(queue_entry_id=row['queue_entry_id'], card_id=row['card_id'], card_revision=row['revision'], issuance_sequence=row['issuance_sequence'], issued_at=issued_at)

def attempt_issuance(database, queue_entry_id, card_id, revision):
    row = database.execute('SELECT queue_entry_id,card_id,revision,issuance_sequence,issued_at\n        FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s AND revision=%s\n          AND issued_as_head=1', (queue_entry_id, card_id, revision)).fetchone()
    return _evidence(row)

def completion_issuance(database, review_id, card_id, revision):
    """Resolve a real saved review through its retained same-attempt receipt."""
    rows = database.execute("SELECT DISTINCT origin.queue_entry_id,origin.card_id,\n            origin.revision,origin.issuance_sequence,origin.issued_at\n        FROM reviews review JOIN review_attempt_receipts receipt ON receipt.review_id=review.id\n        JOIN queue_attempt_origins origin ON origin.queue_entry_id=receipt.queue_entry_id\n          AND origin.card_id=receipt.card_id AND origin.card_id=review.card_id\n        WHERE review.id=%s AND origin.card_id=%s AND origin.revision=%s\n          AND origin.issued_as_head=1 AND review.source_kind IN ('study','position_study')\n          AND review.invalidated_at IS NULL LIMIT 2", (review_id, card_id, revision)).fetchall()
    return _evidence(rows[0]) if len(rows) == 1 else None

def committed_issuance_count(database, determined_at, *, through_issuance_sequence=None) -> int:
    """Observe genuine committed issuances since the original determination.

    This read works before the target is issued. Each retained queue generation
    counts once, across clients and Study days; prefetch, old unknown issuances
    and unused sequence values do not count. A ceiling reproduces the target's
    ordinal without treating its monotone fence as a count.
    """
    ceiling = ' AND issuance_sequence<=%s' if through_issuance_sequence is not None else ''
    parameters = (determined_at, through_issuance_sequence) if through_issuance_sequence is not None else (determined_at,)
    return int(database.execute('SELECT COUNT(*) FROM queue_attempt_origins\n        WHERE issued_as_head=1 AND issued_at>%s AND issuance_sequence IS NOT NULL' + ceiling, parameters).fetchone()[0])

def first_target_issuance(database, card_id, revision, determined_at, *, after_issuance_sequence=0):
    """Return actual target authority and its card ordinal after determination.

    Count committed distinct origins rather than sequence subtraction, queue
    positions or client sessions. An already-issued current attempt is excluded.
    """
    row = database.execute('SELECT queue_entry_id,card_id,revision,issuance_sequence,issued_at\n        FROM queue_attempt_origins WHERE card_id=%s AND revision=%s AND issued_as_head=1\n          AND issued_at>%s AND issuance_sequence>%s ORDER BY issuance_sequence LIMIT 1', (card_id, revision, determined_at, after_issuance_sequence)).fetchone()
    evidence = _evidence(row)
    if evidence is None:
        return None
    return (evidence, committed_issuance_count(database, determined_at, through_issuance_sequence=evidence.issuance_sequence))
