"""Existing fixed capacity SQL and bounded row reads; route policy remains application-owned."""

def _available_route_card(alias: str) -> str:
    return f"""{alias}.content_type='opening' AND {alias}.archived=0
        AND COALESCE({alias}.pending_validation,0)=0
        AND ({alias}.repertoire_id=step.repertoire_id OR EXISTS(
            SELECT 1 FROM repertoire_cards membership WHERE membership.card_id={alias}.id
              AND membership.repertoire_id=step.repertoire_id))
        AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id={alias}.id)
        AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks blocked
                       WHERE blocked.card_id={alias}.id AND blocked.repertoire_id=step.repertoire_id)"""

_CAPACITY_ROUTE_FROM = f"""FROM cards parent
    JOIN opening_graph_steps step ON step.parent_card_id=parent.id
    JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
      AND publication.generation=step.generation AND publication.state='ready'
    JOIN repertoires repertoire ON repertoire.id=step.repertoire_id
    JOIN repertoire_lines line ON line.id=step.line_id AND line.repertoire_id=step.repertoire_id
    JOIN opening_graph_steps parent_step ON parent_step.repertoire_id=step.repertoire_id
      AND parent_step.generation=step.generation AND parent_step.line_id=step.line_id
      AND parent_step.decision_index=step.decision_index-1 AND parent_step.card_id=parent.id
    JOIN cards candidate ON candidate.id=step.card_id
    WHERE {_available_route_card("candidate")} AND {_available_route_card("parent")}
      AND parent.state IN ('new','learning','mature')
      AND candidate.state='locked' AND candidate.introduced_at IS NULL
      AND step.segment_kind='decision' AND step.first_decision_index=step.last_decision_index
      AND step.first_decision_index=parent_step.last_decision_index+1
      AND step.trained_color=parent_step.trained_color
      AND step.card_id<>parent.id
      AND (SELECT COUNT(*) FROM opening_graph_steps earlier
           WHERE earlier.repertoire_id=step.repertoire_id AND earlier.generation=step.generation
             AND earlier.line_id=step.line_id AND earlier.decision_index<parent_step.decision_index
             AND earlier.decision_index>=0)=parent_step.decision_index
      AND EXISTS(SELECT 1 FROM opening_graph_steps root WHERE root.repertoire_id=step.repertoire_id
          AND root.generation=step.generation AND root.line_id=step.line_id
          AND root.decision_index=0 AND root.segment_kind='prefix'
          AND root.parent_card_id IS NULL AND root.first_decision_index=0)
      AND NOT EXISTS(
          SELECT 1 FROM opening_graph_steps earlier LEFT JOIN cards prerequisite ON prerequisite.id=earlier.card_id
          WHERE earlier.repertoire_id=step.repertoire_id AND earlier.generation=step.generation
            AND earlier.line_id=step.line_id AND earlier.decision_index<parent_step.decision_index
            AND (prerequisite.id IS NULL OR NOT ({_available_route_card("prerequisite")})
              OR NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=earlier.card_id
                            AND review.source_kind='study' AND review.invalidated_at IS NULL)))
      AND NOT EXISTS(
          SELECT 1 FROM opening_graph_steps later LEFT JOIN opening_graph_steps earlier
            ON earlier.repertoire_id=later.repertoire_id AND earlier.generation=later.generation
            AND earlier.line_id=later.line_id AND earlier.decision_index=later.decision_index-1
          WHERE later.repertoire_id=step.repertoire_id AND later.generation=step.generation
            AND later.line_id=step.line_id AND later.decision_index BETWEEN 1 AND parent_step.decision_index
            AND (earlier.card_id IS NULL OR later.parent_card_id<>earlier.card_id
              OR later.parent_card_id IS NULL OR later.segment_kind<>'decision'
              OR later.first_decision_index<>later.last_decision_index
              OR later.first_decision_index<>earlier.last_decision_index+1
              OR later.trained_color<>earlier.trained_color))
"""


def read_capacity_route_rows(
    read, repertoire_id: str, normal_parent_ids: tuple[str, ...], batch_size: int,
):
    """Page unique direct children, then retain one deterministic route per card.

    read closes each connection before returning; only frontier metadata escapes.
    """
    parent_ids = set(normal_parent_ids)
    after_parent_id = ""
    while True:
        parent_page = [
            row[0]
            for row in read(
                "SELECT parent.id FROM cards parent WHERE parent.content_type='opening' AND parent.introduced_at IS NOT NULL AND parent.state IN ('new','learning','mature') AND parent.archived=0 AND COALESCE(parent.pending_validation,0)=0 AND (parent.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards membership WHERE membership.card_id=parent.id AND membership.repertoire_id=%s)) AND NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=parent.id AND review.source_kind='study' AND review.invalidated_at IS NULL) AND parent.id>%s ORDER BY parent.id LIMIT %s",
                (
                    repertoire_id,
                    repertoire_id,
                    after_parent_id,
                    batch_size,
                ),
            )
        ]
        if not parent_page:
            break
        parent_ids.update(parent_page)
        after_parent_id = parent_page[-1]
    ordered_parent_ids = sorted(parent_ids)
    for offset in range(0, len(ordered_parent_ids), batch_size):
        parent_page = ordered_parent_ids[offset : offset + batch_size]
        parameters = (repertoire_id, *parent_page)
        route_from = (
            _CAPACITY_ROUTE_FROM
            + ' AND step.repertoire_id=%s AND parent.id IN ('
            + ",".join('%s' for _ in parent_page)
            + ")"
        )
        after_card_id = ""
        while True:
            identifiers = [
                row[0]
                for row in read(
                    "SELECT DISTINCT candidate.id "
                    + route_from
                    + ' AND candidate.id>%s ORDER BY candidate.id LIMIT %s',
                    (*parameters, after_card_id, batch_size),
                )
            ]
            if not identifiers:
                break
            rows = read(
                "SELECT card_id,repertoire_id,generation,line_id,decision_index,parent_card_id,parent_introduced FROM ("
                "SELECT candidate.id card_id,step.repertoire_id,step.generation,step.line_id,step.decision_index,"
                "parent.id parent_card_id,CASE WHEN parent.introduced_at IS NOT NULL THEN 1 ELSE 0 END parent_introduced,"
                "ROW_NUMBER() OVER(PARTITION BY candidate.id ORDER BY CASE WHEN parent.introduced_at IS NOT NULL "
                "THEN 0 ELSE 1 END,step.line_id,step.decision_index,parent.id) route_order "
                + route_from
                + " AND candidate.id IN ("
                + ",".join('%s' for _ in identifiers)
                + ")) ranked WHERE route_order=1",
                (*parameters, *identifiers),
            )
            for row in rows:
                yield row
            after_card_id = identifiers[-1]



def queued_capacity_card(database, day, card_id):
    return database.execute(
        'SELECT 1 FROM daily_queue WHERE queue_date=%s AND card_id=%s',
        (day, card_id),
    ).fetchone()


def current_capacity_priority(database, card_id, day, repertoire_id):
    return database.execute(
        'SELECT 1 FROM cards c WHERE c.id=%s AND c.due_date<=%s',
        (card_id, day),
    ).fetchone()


def published_capacity_route(database, card_id, repertoire_id, generation, line_id, decision_index, parent_card_id):
    return database.execute(
            "SELECT 1 " + _CAPACITY_ROUTE_FROM + ' AND parent.introduced_at IS NOT NULL AND step.card_id=%s AND step.repertoire_id=%s AND step.generation=%s AND step.line_id=%s AND step.decision_index=%s AND parent.id=%s',
            (
                card_id,
                repertoire_id,
                generation,
                line_id,
                decision_index,
                parent_card_id,
            ),
        ).fetchone()
