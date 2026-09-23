"""Turn canonical real-game misses into targeted study without rating memory."""

from __future__ import annotations

from datetime import date, datetime, timezone
import sqlite3

from ..database import connection
from .activity_gate import activity_gate
from .review_service import ensure_card_queued_after


MISS_REASON = "Priority review · missed in a recent game"


def _utc_instant(timestamp: str) -> datetime:
    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant.astimezone(timezone.utc)


def prioritize_real_game_miss(database: sqlite3.Connection, event_id: str) -> bool:
    """Queue one eligible studied card; replay keeps its existing queue position."""
    missed_event = database.execute(
        """SELECT event.card_id,event.played_at
           FROM repertoire_decision_events event
           JOIN imported_games game ON game.id=event.game_id
           JOIN cards card ON card.id=event.card_id
           WHERE event.id=? AND event.outcome='miss' AND game.adaptive_excluded=0
             AND card.archived=0 AND card.content_type='opening'
             AND COALESCE(card.pending_validation,0)=0
             AND NOT EXISTS(
                 SELECT 1 FROM repertoire_integrity_card_blocks block
                 WHERE block.card_id=card.id AND block.repertoire_id=event.repertoire_id
             )""",
        (event_id,),
    ).fetchone()
    if not missed_event:
        return False
    latest_study = database.execute(
        """SELECT reviewed_at FROM reviews WHERE card_id=? AND source_kind='study'
           ORDER BY julianday(reviewed_at) DESC,id DESC LIMIT 1""",
        (missed_event["card_id"],),
    ).fetchone()
    if not latest_study or _utc_instant(missed_event["played_at"]) <= _utc_instant(latest_study["reviewed_at"]):
        return False
    if database.execute(
        "SELECT 1 FROM reviews WHERE source_kind='game' AND source_ref=? LIMIT 1",
        (event_id,),
    ).fetchone():
        return False
    existing_queue_entry = database.execute(
        """SELECT gameplay_priority_reason FROM daily_queue
           WHERE queue_date=? AND card_id=? AND status='queued'
           ORDER BY position,id LIMIT 1""",
        (date.today().isoformat(), missed_event["card_id"]),
    ).fetchone()
    if existing_queue_entry and existing_queue_entry["gameplay_priority_reason"] == MISS_REASON:
        return True
    ensure_card_queued_after(database, missed_event["card_id"], 4, priority_reason=MISS_REASON)
    return True


def apply_real_game_misses(game_id: str, *, background: bool = False) -> None:
    """Promote valid studied-card misses; unseen cards use introduction priority."""
    with connection(background=background) as database:
        missed_event_ids = [row[0] for row in database.execute(
            """SELECT id FROM repertoire_decision_events
               WHERE game_id=? AND outcome='miss' ORDER BY ply,id""",
            (game_id,),
        )]
    for event_id in missed_event_ids:
        if background:
            activity_gate.wait_for_foreground()
        with connection(background=background) as database:
            prioritize_real_game_miss(database, event_id)
