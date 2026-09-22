"""Apply one game-sourced lapse per studied card and preserve decision history."""

from __future__ import annotations

from datetime import date, datetime, timezone

from ..database import connection
from .activity_gate import activity_gate
from .review_service import apply_scheduling_review, ensure_card_queued_after


MISS_REASON = "Priority review · missed in a recent game"


def apply_real_game_misses(game_id: str, *, background: bool = False) -> None:
    """Advance studied cards once per study cycle; unseen cards use introduction priority."""
    with connection(background=background) as database:
        missed_events = [dict(row) for row in database.execute(
            """SELECT event.id,event.card_id,event.played_at
               FROM repertoire_decision_events event
               JOIN imported_games game ON game.id=event.game_id
               JOIN cards card ON card.id=event.card_id
               WHERE event.game_id=? AND event.outcome='miss'
                 AND game.adaptive_excluded=0 AND card.archived=0
                 AND card.content_type='opening'
                 AND COALESCE(card.pending_validation,0)=0
                 AND NOT EXISTS(
                     SELECT 1 FROM repertoire_integrity_card_blocks block
                     WHERE block.card_id=card.id AND block.repertoire_id=event.repertoire_id
                 )
               ORDER BY event.ply,event.id""",
            (game_id,),
        )]
    for missed_event in missed_events:
        if background:
            activity_gate.wait_for_foreground()
        with connection(background=background) as database:
            study = database.execute(
                """SELECT MAX(reviewed_at) AS latest_study_at FROM reviews
                   WHERE card_id=? AND source_kind='study'""",
                (missed_event["card_id"],),
            ).fetchone()
            latest_study_at = study["latest_study_at"]
            if not latest_study_at:
                continue
            played_at = datetime.fromisoformat(missed_event["played_at"])
            studied_at = datetime.fromisoformat(latest_study_at)
            if played_at.tzinfo is None:
                played_at = played_at.replace(tzinfo=timezone.utc)
            if studied_at.tzinfo is None:
                studied_at = studied_at.replace(tzinfo=timezone.utc)
            if played_at <= studied_at:
                continue
            already_counted = database.execute(
                """SELECT 1 FROM reviews WHERE card_id=? AND source_kind='game'
                   AND datetime(reviewed_at)>=datetime(?) LIMIT 1""",
                (missed_event["card_id"], latest_study_at),
            ).fetchone()
            if already_counted:
                continue
            light_days = database.execute(
                "SELECT light_first_interval_days FROM settings WHERE id=1"
            ).fetchone()[0]
            now = datetime.now(timezone.utc)
            apply_scheduling_review(
                database,
                missed_event["card_id"],
                "again",
                guided=False,
                source_kind="game",
                source_ref=missed_event["id"],
                light_first_interval_days=light_days,
                reviewed_at=now,
                review_day=date.today(),
            )
            ensure_card_queued_after(
                database, missed_event["card_id"], 4, priority_reason=MISS_REASON
            )
