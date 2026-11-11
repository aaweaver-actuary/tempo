"""Bounded native source locks for current repertoire editing."""

def lock_sources(database, repertoire_id: str) -> tuple[dict, ...]:
    """Current authored sources, bounded and serialized with repertoire writers."""
    if database.execute_native("SELECT id FROM repertoires WHERE id=%s FOR UPDATE",
                               (repertoire_id,)).fetchone() is None:
        return ()
    rows = database.execute_native("""
        SELECT 'line:' || id source_id, start_fen, moves_json, trained_color
        FROM repertoire_lines WHERE repertoire_id=%s
        UNION ALL
        SELECT 'card:' || card.id, card.start_fen, card.moves_json,
            COALESCE(card.trained_color, (SELECT line.trained_color FROM repertoire_lines line
                WHERE line.repertoire_id=%s ORDER BY line.created_at,line.id LIMIT 1))
        FROM cards card WHERE card.archived=0 AND card.content_type='opening'
            AND card.superseded_by IS NULL AND (card.repertoire_id=%s OR EXISTS(
                SELECT 1 FROM repertoire_cards link WHERE link.repertoire_id=%s AND link.card_id=card.id))
        ORDER BY source_id LIMIT 10001
        """, (repertoire_id, repertoire_id, repertoire_id, repertoire_id)).fetchall()
    if len(rows) > 10000:
        raise ValueError("source_exceeds_bound")
    return tuple(dict(row) for row in rows)
