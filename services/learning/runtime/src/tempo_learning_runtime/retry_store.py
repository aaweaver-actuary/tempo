"""Borrowed-connection persistence for one completed review's retry decision.

Both server compositions use this SQL adapter. The review application owns the
pure policy; each composition supplies its existing queue insertion operation.
"""
from datetime import datetime, timezone
from pathlib import Path
import secrets

from tempo_learning_review.retry_policy import FailedRetryRequest, plan_failed_retry
from tempo_learning_review.retry_policy import QueueIdentity, QueueReadyFacts, RetryCandidate

from .real_game_feedback import MISS_REASON


def initialize_sqlite_retry_policies(database) -> None:
    # This compatibility initializer never commits its caller's transaction.
    source = (Path(__file__).parent / "migrations/0004_soft_failed_retries.sql").read_text()
    for statement in source.split(";"):
        if statement.strip():
            statement = statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
            statement = statement.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
            database.execute(statement)


def random_retry_draws() -> tuple[int, str]:
    return 300 + secrets.randbelow(301), secrets.token_hex(16)


def persist_failed_retry(database, request: FailedRetryRequest, *, create_entry,
                         draws=None) -> int:
    """Persist once, after history append and within its original transaction."""
    source = request.source_attempt
    reviewed = database.execute(
        "SELECT card_id,rating,reviewed_at,invalidated_at FROM reviews WHERE id=?",
        (request.review_id,),
    ).fetchone()
    if (not reviewed or reviewed["card_id"] != source.card_id
            or reviewed["rating"] != "again" or reviewed["invalidated_at"] is not None):
        raise ValueError("Failed retry requires its valid completed Again review")
    original_time = datetime.fromisoformat(reviewed["reviewed_at"].replace("Z", "+00:00"))
    if original_time != request.reviewed_at:
        raise ValueError("Failed retry must retain the original review time")
    prior = database.execute("SELECT * FROM queue_retry_policies WHERE review_id=?",
                             (request.review_id,)).fetchone()
    if prior:
        if (prior["source_queue_entry_id"], prior["card_id"], prior["revision"]) != (
                source.id, source.card_id, source.revision):
            raise ValueError("Failed retry review identity was rebound")
        return int(prior["retry_queue_entry_id"])
    delay_seconds, order_token = (draws or random_retry_draws)()
    plan = plan_failed_retry(request, delay_seconds=delay_seconds, order_token=order_token)
    entry_id = create_entry()
    priority = database.execute("SELECT gameplay_priority_reason FROM daily_queue WHERE id=?",
                                (source.id,)).fetchone()
    if priority and priority[0]:
        database.execute("UPDATE daily_queue SET gameplay_priority_reason=? WHERE id=?",
                         (priority[0], entry_id))
    database.execute(
        """INSERT INTO queue_retry_policies(review_id,source_queue_entry_id,retry_queue_entry_id,
               card_id,revision,policy_version,delay_seconds,ready_at,order_token)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (request.review_id, source.id, entry_id, source.card_id, source.revision,
         plan.policy_version, plan.delay_seconds, plan.ready_at.astimezone(timezone.utc).isoformat(),
         plan.order_token),
    )
    return int(entry_id)


def _prepare_instant_query(database) -> None:
    # Reuse the compositions' real_game_instant SQL primitive: native adapters
    # translate it to timestamptz, while SQLite needs exact integer microseconds.
    if hasattr(database, "execute_native"):
        return
    raw = getattr(database, "database", database)
    def instant(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        elapsed = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
    raw.create_function("real_game_instant", 1, instant, deterministic=True)


_ISSUED = """EXISTS(SELECT 1 FROM queue_attempt_origins issued
    WHERE issued.queue_entry_id=q.id AND issued.card_id=c.id
      AND issued.revision=c.revision AND issued.issued_as_head=1)"""
_CURRENT_POLICY = """retry.card_id=c.id AND retry.revision=c.revision
    AND (retry.released_at IS NULL OR retry.retry_queue_entry_id!=q.id)
    AND failed.invalidated_at IS NULL
    AND failed.rating='again'
    AND COALESCE(q.admission_kind,'')!='explicit'
    AND NOT EXISTS(SELECT 1 FROM reviews later
        WHERE later.card_id=retry.card_id AND later.invalidated_at IS NULL
          AND (real_game_instant(later.reviewed_at)>real_game_instant(failed.reviewed_at)
            OR (real_game_instant(later.reviewed_at)=real_game_instant(failed.reviewed_at)
                AND later.id>failed.id)))"""
_DUE_WORK = """(c.due_date<=q.queue_date OR q.admission_kind='explicit'
    OR (q.gameplay_priority_reason IS NOT NULL
        AND (q.cycle=0 OR q.attempt_state='gameplay')))"""
_DETACHED_ISSUED = """(retry.retry_queue_entry_id!=q.id AND EXISTS(
    SELECT 1 FROM queue_attempt_origins retained
    WHERE retained.queue_entry_id=retry.retry_queue_entry_id
      AND retained.card_id=retry.card_id AND retained.revision=retry.revision
      AND retained.queue_date=q.queue_date
      AND retained.issued_as_head=1 AND retained.last_status='queued'))"""


def ready_attempt_sql(database, base_statement: str) -> str:
    """Serving readiness only. Base admission/presentation eligibility is unchanged."""
    _prepare_instant_query(database)
    return base_statement + " AND (" + _ISSUED + " OR (" + _DUE_WORK + " AND NOT EXISTS(" + (
        "SELECT 1 FROM queue_retry_policies retry JOIN reviews failed ON failed.id=retry.review_id "
        "WHERE " + _CURRENT_POLICY + ")))")


def _identity(row, day):
    return QueueIdentity(row['id'], row['card_id'], row['revision'], row['cycle'], day,
                         bool(row.get('issued', False)))


def pending_retry_sql(base_statement: str, columns: str) -> str:
    statement = base_statement.replace("SELECT q.id FROM", "SELECT " + columns + " FROM", 1)
    statement = statement.replace("JOIN cards c ON c.id=q.card_id",
        "JOIN cards c ON c.id=q.card_id JOIN queue_retry_policies retry ON retry.card_id=c.id "
        "JOIN reviews failed ON failed.id=retry.review_id", 1)
    return statement + " AND " + _CURRENT_POLICY + " AND NOT " + _ISSUED + " AND c.due_date<=q.queue_date"


def queue_ready_facts(database, day: str, observed_at: datetime, *, base_statement: str,
                      complete: bool) -> QueueReadyFacts:
    """Unpaged indexed authority reads; caller must recheck under issuance locks.

    `complete` is an essential-queue projection fact from the composition, never
    an HTTP request or client snapshot. No SKIP LOCKED query establishes absence.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Queue observation must include a timezone")
    _prepare_instant_query(database)
    fields = "q.id,q.card_id,q.cycle,c.revision," + _ISSUED + " issued"
    statement = ready_attempt_sql(database, base_statement).replace("SELECT q.id FROM", "SELECT " + fields + " FROM", 1)
    row = database.execute(statement + " ORDER BY q.position,q.id LIMIT 1", (day,)).fetchone()
    head = _identity(dict(row), day) if row else None
    if head and head.issued:
        return QueueReadyFacts(head, None, None, complete)
    candidates = pending_retry_sql(base_statement, fields + ",retry.ready_at,retry.order_token,retry.released_at,q.gameplay_priority_reason")
    detached = database.execute(candidates + ' AND ' + _DETACHED_ISSUED + ' LIMIT 1',
                                (day,)).fetchone()
    pending_reason = 'issued_retry_origin_missing' if detached else None
    complete = complete and not detached
    candidates += ' AND NOT ' + _DETACHED_ISSUED
    order = " ORDER BY CASE WHEN q.gameplay_priority_reason=? THEN 0 ELSE 1 END,retry.order_token,q.id LIMIT 1"
    timestamp = observed_at.astimezone(timezone.utc).isoformat()
    def candidate(comparison):
        condition = ('(retry.released_at IS NOT NULL OR real_game_instant(retry.ready_at)<=real_game_instant(?))'
                     if comparison=='<=' else
                     '(retry.released_at IS NULL AND real_game_instant(retry.ready_at)>real_game_instant(?))')
        row = database.execute(candidates + " AND " + condition + order,
                               (day, timestamp, MISS_REASON)).fetchone()
        if row is None:
            return None
        values = dict(row)
        return RetryCandidate(_identity(values, day), datetime.fromisoformat(values['ready_at']),
                              values['order_token'], values['gameplay_priority_reason'] == MISS_REASON,
                              datetime.fromisoformat(values['released_at']) if values['released_at'] else None)
    due = candidate("<=")
    cooling = candidate(">") if head is None and due is None and complete else None
    return QueueReadyFacts(head, due, cooling, bool(complete), pending_reason)
