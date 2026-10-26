"""Bounded physical-binding leads for an explicit source game position.

This is discovery only: retain conflicting, missing, blocked and unintroduced
facts. A lead cannot authorize feedback; the existing complete authority capture
and receiver must still validate the actual game/ply and current source.
"""

MAX_BINDINGS = 64


class CandidateOverflow(ValueError):
    """No partial candidate set may be treated as complete."""


def decision_bindings(reader, repertoire_id: str, graph_generation: int,
                      position_key: str) -> tuple[dict, ...]:
    """Read <=64 graph/card/route bindings, refusing a 65th result.

    The caller replays its explicit canonical game to obtain position_key and
    uses the existing current-authority metadata/certificate. The reader is the
    owner's existing short background read seam; no connection/transaction is
    opened, committed, or retained here. No eligibility policy or writes occur.
    """
    if (not isinstance(repertoire_id, str) or not 1 <= len(repertoire_id) <= 200
            or type(graph_generation) is not int or graph_generation < 1
            or not isinstance(position_key, str) or not 1 <= len(position_key) <= 200):
        raise ValueError("one_explicit_current_position_required")
    rows = reader("""SELECT step.repertoire_id,step.generation,step.line_id,
        step.decision_index,step.card_id,step.starting_fen AS graph_start_fen,
        step.moves_json AS graph_moves_json,step.trained_color AS graph_color,
        step.first_decision_index,step.last_decision_index,step.decision_fen_keys_json,
        line.start_fen AS route_start_fen,line.moves_json AS route_moves_json,
        line.trained_color AS route_color,
        card.revision,card.start_fen,card.moves_json,card.trained_color,
        card.content_type,card.archived,card.pending_validation,card.superseded_by,
        card.introduced_at,card.canonical_route_source,
        EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.repertoire_id=step.repertoire_id
            AND link.card_id=step.card_id) AS has_membership,
        EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
            WHERE block.repertoire_id=step.repertoire_id AND block.card_id=step.card_id) AS integrity_blocked,
        EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=step.card_id) AS deleted
        FROM opening_graph_steps step
        JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
            AND publication.generation=step.generation
        JOIN repertoire_lines line ON line.id=step.line_id
        LEFT JOIN cards card ON card.id=step.card_id
        WHERE step.repertoire_id=%s AND step.generation=%s
            AND step.decision_fen_keys_json::jsonb ? %s
        ORDER BY step.line_id,step.decision_index LIMIT 65""",
        (repertoire_id, graph_generation, position_key))
    if len(rows) > MAX_BINDINGS:
        raise CandidateOverflow("decision_binding_candidate_budget")
    return tuple(dict(row) for row in rows)


def introduced_card_lead(reader, repertoire_id: str, graph_generation: int) -> dict | None:
    """Pick at most one oldest introduced card as a position-discovery lead only.

    This deliberately is not an alternatives/eligibility projection. Re-read all
    alternatives with decision_bindings for the chosen physical position, then
    run unchanged authority capture; this row alone cannot authorize anything.
    """
    if (not isinstance(repertoire_id, str) or not 1 <= len(repertoire_id) <= 200
            or type(graph_generation) is not int or graph_generation < 1):
        raise ValueError("one_explicit_current_publication_required")
    rows = reader("""SELECT step.repertoire_id,step.generation,step.line_id,
        step.decision_index,step.card_id,card.revision,card.start_fen,card.moves_json,
        card.trained_color,card.introduced_at,step.decision_fen_keys_json
        FROM opening_graph_steps step
        JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
            AND publication.generation=step.generation
        JOIN cards card ON card.id=step.card_id
        WHERE step.repertoire_id=%s AND step.generation=%s AND card.introduced_at IS NOT NULL
        ORDER BY card.introduced_at,step.line_id,step.decision_index LIMIT 1""",
        (repertoire_id, graph_generation))
    if len(rows) > 1:
        raise CandidateOverflow("introduced_card_lead_budget")
    return dict(rows[0]) if rows else None
