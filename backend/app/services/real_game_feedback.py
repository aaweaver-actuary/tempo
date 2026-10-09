"""Turn canonical real-game misses into ordinary study obligations, never reviews."""

from __future__ import annotations

from datetime import date, datetime, timezone
import sqlite3

from ..database import connection
from ..postgres_store import PostgresConnection
from ..queue_position_lock import lock_queue_date_for_position
from .activity_gate import activity_gate
from .review_service import ensure_card_queued_after, preserve_daily_queue_order


MISS_REASON = "Priority review · missed in a recent game"


def _utc_instant(timestamp: str) -> datetime:
    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant.astimezone(timezone.utc)


def _instant_microseconds(timestamp: str) -> int:
    elapsed = _utc_instant(timestamp) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds


def outstanding_miss_sql(*, postgres: bool = False, columns: str = "DISTINCT event.card_id",
                         additional_where: str = "") -> str:
    """One predicate for current, safe, unremediated opening evidence.

    Callers supply only fixed SQL fragments, never request values. Native PostgreSQL
    instants and SQLite's integer microseconds avoid julianday's precision loss.
    """
    study_instant = "CAST(review.reviewed_at AS TIMESTAMPTZ)" if postgres else "real_game_instant(review.reviewed_at)"
    miss_instant = "CAST(event.played_at AS TIMESTAMPTZ)" if postgres else "real_game_instant(event.played_at)"
    return f"""SELECT {columns}
        FROM current_repertoire_decision_events event
        JOIN imported_games game ON game.id=event.game_id
        JOIN cards card ON card.id=event.card_id
        WHERE event.outcome='miss' AND game.adaptive_excluded=0
          AND card.content_type='opening' AND card.archived=0
          AND COALESCE(card.pending_validation,0)=0 AND card.superseded_by IS NULL
          AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id)
          AND EXISTS(SELECT 1 FROM repertoires repertoire WHERE repertoire.id=event.repertoire_id)
          AND (card.repertoire_id=event.repertoire_id OR EXISTS(
              SELECT 1 FROM repertoire_cards link
              WHERE link.card_id=card.id AND link.repertoire_id=event.repertoire_id))
          AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                         WHERE block.card_id=card.id AND block.repertoire_id=event.repertoire_id)
          AND NOT EXISTS(SELECT 1 FROM reviews review
                         WHERE review.card_id=card.id AND review.source_kind='study'
                           AND review.invalidated_at IS NULL AND {study_instant}>={miss_instant})
          {additional_where}"""


def outstanding_miss_query(database, **options) -> str:
    """Prepare the same predicate for a production or compatibility connection."""
    if not isinstance(database, PostgresConnection):
        sqlite_database = getattr(database, "database", database)
        sqlite_database.create_function("real_game_instant", 1, _instant_microseconds, deterministic=True)
    return outstanding_miss_sql(postgres=isinstance(database, PostgresConnection), **options)


def has_outstanding_real_game_miss(database, card_id: str) -> bool:
    return database.execute(outstanding_miss_query(database, columns="1", additional_where="AND card.id=? LIMIT 1"),
                            (card_id,)).fetchone() is not None


def prioritize_queued_misses(database, queue_date: str) -> None:
    """Apply a stable priority overlay while preserving ordinary queue order."""
    entries = database.execute(
        "SELECT id,card_id,gameplay_priority_reason FROM daily_queue "
        "WHERE queue_date=? AND status='queued' ORDER BY position,id", (queue_date,),
    ).fetchall()
    priority_entries = sorted((entry for entry in entries if entry["gameplay_priority_reason"] == MISS_REASON),
                              key=lambda entry: (entry["card_id"], entry["id"]))
    ordinary_entries = [entry for entry in entries if entry["gameplay_priority_reason"] != MISS_REASON]
    ordered = priority_entries + ordinary_entries
    if [entry["id"] for entry in ordered] != [entry["id"] for entry in entries]:
        for position, entry in enumerate(ordered):
            database.execute("UPDATE daily_queue SET position=? WHERE id=?", (position, entry["id"]))
    preserve_daily_queue_order(database, queue_date)


def promote_real_game_card(database, card_id: str, queue_date: str) -> bool:
    """Recheck one obligation under the card lock; introduction uses admission."""
    if isinstance(database, PostgresConnection):
        database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{card_id}",))
        database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))
    outstanding = database.execute(
        outstanding_miss_query(database, columns="card.state,card.introduced_at", additional_where="AND card.id=? LIMIT 1"),
        (card_id,),
    ).fetchone()
    if outstanding is None:
        return False
    if database.execute("SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=? AND status='buried' LIMIT 1",
                        (queue_date, card_id)).fetchone():
        return False
    if outstanding["state"] in ("new", "locked") and outstanding["introduced_at"] is None:
        from ..queue_commands import request_queue_refresh_in_transaction
        request_queue_refresh_in_transaction(database, queue_date)
        return True
    if outstanding["state"] not in ("learning", "mature"):
        return False
    if isinstance(database, PostgresConnection):
        lock_queue_date_for_position(database, queue_date)
    ensure_card_queued_after(database, card_id, 0, priority_reason=MISS_REASON, queue_date=queue_date)
    prioritize_queued_misses(database, queue_date)
    return True


def prioritize_real_game_miss(database: sqlite3.Connection, event_id: str) -> bool:
    """Replay one current miss through the ordinary admission/review machinery."""
    event = database.execute("SELECT card_id FROM current_repertoire_decision_events WHERE id=? AND outcome='miss'", (event_id,)).fetchone()
    return bool(event and event["card_id"] and promote_real_game_card(database, event["card_id"], date.today().isoformat()))


def clear_satisfied_miss_priority(database, card_id: str) -> None:
    if not has_outstanding_real_game_miss(database, card_id):
        database.execute("UPDATE daily_queue SET gameplay_priority_reason=NULL "
                         "WHERE card_id=? AND gameplay_priority_reason=?", (card_id, MISS_REASON))


def apply_real_game_misses(game_id: str, *, background: bool = False) -> None:
    """Apply one decision in each short, foreground-preemptible transaction."""
    with connection(background=background) as database:
        missed_event_ids = [row[0] for row in database.execute(
            "SELECT id FROM current_repertoire_decision_events WHERE game_id=? AND outcome='miss' ORDER BY ply,id",
            (game_id,),
        )]
    for event_id in missed_event_ids:
        if background:
            activity_gate.wait_for_foreground()
        with connection(background=background) as database:
            prioritize_real_game_miss(database, event_id)
