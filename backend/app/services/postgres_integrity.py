"""PostgreSQL integrity generation boundaries for repertoire edits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..database import background_read_connection
from ..postgres_store import PostgresConnection
from .repertoire_integrity import _scan_source


@dataclass(frozen=True)
class PreparedIntegritySource:
    source_type: str
    source_id: str
    positions: tuple[dict[str, Any], ...]
    invalid: tuple[dict[str, Any], ...]


def prepare_next_integrity_source(
    repertoire_id: str, source_type: str, after_source_id: str,
) -> PreparedIntegritySource | None:
    """Read one line or card, close PostgreSQL, then traverse its moves."""

    if source_type not in {"line", "card"}:
        raise ValueError("Unknown integrity source type")
    with background_read_connection() as database:
        first_line = database.execute_native(
            "SELECT trained_color FROM repertoire_lines WHERE repertoire_id=%s "
            "ORDER BY created_at,id LIMIT 1", (repertoire_id,),
        ).fetchone()
        repertoire_color = str(first_line[0]) if first_line else None
        if source_type == "line":
            source_row = database.execute_native(
                "SELECT id,repertoire_id,name,trained_color,start_fen,moves_json,created_at "
                "FROM repertoire_lines WHERE repertoire_id=%s AND id>%s "
                "ORDER BY id LIMIT 1", (repertoire_id, after_source_id),
            ).fetchone()
        else:
            source_row = database.execute_native(
                "SELECT card.id,card.repertoire_id,card.kind,card.start_fen,"
                "card.moves_json,COALESCE(card.trained_color,%s) trained_color "
                "FROM cards card WHERE card.id>%s AND card.archived=0 "
                "AND card.content_type='opening' AND "
                "(card.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards link "
                "WHERE link.repertoire_id=%s AND link.card_id=card.id)) "
                "ORDER BY card.id LIMIT 1",
                (repertoire_color, after_source_id, repertoire_id, repertoire_id),
            ).fetchone()
        source = dict(source_row) if source_row else None
    if source is None:
        return None
    source["source_type"] = source_type
    source["source_id"] = source["id"]
    positions, invalid = _scan_source(source, repertoire_color)
    return PreparedIntegritySource(
        source_type, source["id"], tuple(positions.values()), tuple(invalid),
    )


def invalidate_integrity_in_transaction(
    database: PostgresConnection, repertoire_id: str,
) -> None:
    """Never expose an old clean scan as the result of newly written lines."""

    database.execute_native(
        "INSERT INTO repertoire_integrity_state("
        "repertoire_id,status,checked_at,scan_status,scan_generation,"
        "scan_completed_sources,scan_total_sources,scan_error) "
        "VALUES(%s,'unchecked',NULL,'idle',NULL,0,0,NULL) "
        "ON CONFLICT(repertoire_id) DO UPDATE SET "
        "status='unchecked',checked_at=NULL,scan_status='idle',"
        "scan_generation=NULL,scan_completed_sources=0,scan_total_sources=0,scan_error=NULL",
        (repertoire_id,),
    )
