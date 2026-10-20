"""Persistence-only miss projections shared by explicit queue read adapters.

This module owns SQL composition, timestamp precision and accepted-scope exclusion.
Application policy uses RealGameFeedbackStore instead of importing these fragments.
"""
from datetime import datetime, timezone
from ..postgres_store import PostgresConnection
from ..services.real_game_feedback_ports import utc_instant

def _instant_microseconds(timestamp: str) -> int:
    elapsed = utc_instant(timestamp) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds


def outstanding_miss_sql(*, postgres: bool = False, columns: str = "DISTINCT event.card_id",
                         additional_where: str = "") -> str:
    """One predicate for current, safe, unremediated opening evidence.

    Callers supply only fixed SQL fragments, never request values. Native PostgreSQL
    instants and SQLite's integer microseconds avoid julianday's precision loss.
    """
    study_instant = "CAST(review.reviewed_at AS TIMESTAMPTZ)" if postgres else "real_game_instant(review.reviewed_at)"
    miss_instant = "CAST(event.played_at AS TIMESTAMPTZ)" if postgres else "real_game_instant(event.played_at)"
    if postgres:
        from .legacy_game_evidence_scope import owned_legacy_scope_sql
        bridge_exclusion = "AND NOT " + owned_legacy_scope_sql()
    else:
        bridge_exclusion = ""
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
          {bridge_exclusion}
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


