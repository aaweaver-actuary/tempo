"""Existing opening eligibility queries; transactions and selection policy are borrowed."""

from tempo_legacy_database.adapters.opening_progression import PRACTICED_OPENING_PARENT_SQL


_OPENING_UNLOCK_ELIGIBILITY_SQL = f"""content_type='opening' AND state='locked' AND archived=0
    AND EXISTS(
        SELECT 1 FROM opening_graph_steps step
        JOIN opening_graph_publications publication
          ON publication.repertoire_id=step.repertoire_id
         AND publication.generation=step.generation
        WHERE step.card_id=cards.id
          AND (step.parent_card_id IS NULL OR EXISTS(
              SELECT 1 FROM cards parent
              WHERE parent.id=step.parent_card_id AND {PRACTICED_OPENING_PARENT_SQL}
          ))
    )"""


_OPENING_UNLOCK_CANDIDATES_SQL = f"""WITH root_candidates AS (
    SELECT DISTINCT card.id FROM opening_graph_publications publication
    JOIN opening_graph_steps step ON step.repertoire_id=publication.repertoire_id
        AND step.generation=publication.generation
    JOIN cards card ON card.id=step.card_id
    WHERE step.parent_card_id IS NULL AND card.content_type='opening'
        AND card.state='locked' AND card.archived=0 AND card.id>?
    ORDER BY card.id LIMIT ?
), practiced_parent_candidates AS (
    SELECT DISTINCT card.id FROM cards parent
    JOIN opening_graph_steps step ON step.parent_card_id=parent.id
    JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
        AND publication.generation=step.generation
    JOIN cards card ON card.id=step.card_id
    WHERE {PRACTICED_OPENING_PARENT_SQL} AND card.content_type='opening'
        AND card.state='locked' AND card.archived=0 AND card.id>?
    ORDER BY card.id LIMIT ?
) SELECT id FROM root_candidates UNION SELECT id FROM practiced_parent_candidates
ORDER BY id LIMIT ?"""


_OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL = f"""WITH root_candidates AS (
    SELECT candidate.id FROM opening_graph_publications publication
    CROSS JOIN LATERAL (
        SELECT DISTINCT card.id FROM opening_graph_steps step
        JOIN cards card ON card.id=step.card_id
        WHERE step.repertoire_id=publication.repertoire_id
          AND step.generation=publication.generation AND step.parent_card_id IS NULL
          AND card.content_type='opening' AND card.state='locked' AND card.archived=0
          AND card.id>%s ORDER BY card.id NULLS FIRST LIMIT %s
    ) candidate
), practiced_parent_candidates AS (
    SELECT candidate.id FROM cards parent
    CROSS JOIN LATERAL (
        SELECT DISTINCT card.id FROM opening_graph_steps step
        JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
          AND publication.generation=step.generation
        JOIN cards card ON card.id=step.card_id
        WHERE step.parent_card_id=parent.id AND card.content_type='opening'
          AND card.state='locked' AND card.archived=0 AND card.id>%s
        ORDER BY card.id NULLS FIRST LIMIT %s
    ) candidate WHERE {PRACTICED_OPENING_PARENT_SQL}
) SELECT id FROM root_candidates UNION SELECT id FROM practiced_parent_candidates
ORDER BY id NULLS FIRST LIMIT %s"""



def uses_native_execution(database):
    return hasattr(database, 'execute_native')


def locked_opening_page(db, after_card_id, read_limit):
    return db.execute_native(
        "SELECT id FROM cards WHERE content_type='opening' AND state='locked' "
        "AND archived=0 AND id>%s ORDER BY id LIMIT %s",
        (after_card_id, read_limit + 1),
    ).fetchall()


def ready_unlock_candidates(db, candidate_ids, batch_size):
    # Keep the projection captured after the first page read and reused by its
    # selected-card write, as in the original bounded unlock operation.
    eligibility_sql = _OPENING_UNLOCK_ELIGIBILITY_SQL.replace(
        'WHERE step.card_id=cards.id', "WHERE publication.state='ready' AND step.card_id=cards.id")
    eligible = db.execute_native(
        "WITH bounded_cards AS MATERIALIZED (SELECT * FROM cards WHERE id=ANY(%s)) "
        "SELECT id FROM bounded_cards cards WHERE " + eligibility_sql +
        " ORDER BY id LIMIT %s", (candidate_ids, batch_size),
    ).fetchall()

    def unlock_selected(selected_ids):
        db.execute("UPDATE cards SET state='new' WHERE " + eligibility_sql +
                   " AND cards.id IN (" + ",".join("?" for _ in selected_ids) + ")", selected_ids)

    return eligible, unlock_selected


def sparse_unlock_candidates(db, after_card_id, batch_size):
    selector = (db.execute_native if hasattr(db, 'execute_native') else db.execute)
    return selector(
        _OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL if hasattr(db, 'execute_native') else _OPENING_UNLOCK_CANDIDATES_SQL,
        (after_card_id or "", batch_size + 1,
         after_card_id or "", batch_size + 1, batch_size + 1),
    ).fetchall()


def unlock_eligible_cards(db, selected_card_ids):
    update_statement = f"UPDATE cards SET state='new' WHERE {_OPENING_UNLOCK_ELIGIBILITY_SQL}"
    if selected_card_ids:
        update_statement += " AND cards.id IN (" + ",".join("?" for _ in selected_card_ids) + ")"
    db.execute(update_statement, selected_card_ids)


def _block_ineligible_opening_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='blocked'
           WHERE queue_date=? AND status='queued' AND card_id IN (
               SELECT c.id FROM cards c
               WHERE c.content_type='opening' AND (
                   COALESCE(c.pending_validation,0)=1 OR NOT EXISTS(
                       SELECT 1 FROM repertoires eligible
                       WHERE (eligible.id=c.repertoire_id OR EXISTS(
                           SELECT 1 FROM repertoire_cards linked
                           WHERE linked.card_id=c.id AND linked.repertoire_id=eligible.id
                       )) AND NOT EXISTS(
                           SELECT 1 FROM repertoire_integrity_card_blocks block
                           WHERE block.repertoire_id=eligible.id AND block.card_id=c.id
                       )
                   )
               )
           )""",
        (day,),
    )


def _block_unapproved_defense_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='blocked'
           WHERE queue_date=? AND status='queued' AND card_id IN (
               SELECT card.id FROM cards card
               JOIN threat_training_candidates candidate ON candidate.card_id=card.id
               WHERE card.content_type='defense' AND
                 (card.pending_validation=1 OR candidate.approved_at IS NULL
                  OR candidate.validation_state NOT IN ('engine_supported','validated_control'))
           )""",
        (day,),
    )


def _restore_eligible_due_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='queued'
           WHERE queue_date=? AND status='blocked' AND card_id IN (
               SELECT c.id FROM cards c
           WHERE c.archived=0 AND c.due_date<=? AND COALESCE(c.pending_validation,0)=0
                 AND (c.content_type!='defense' OR EXISTS(
                     SELECT 1 FROM threat_training_candidates candidate
                     WHERE candidate.card_id=c.id AND candidate.approved_at IS NOT NULL
                       AND candidate.validation_state IN ('engine_supported','validated_control')
                       AND candidate.superseded_at IS NULL
                 ))
                 AND EXISTS(
                     SELECT 1 FROM repertoires eligible
                     WHERE (eligible.id=c.repertoire_id OR EXISTS(
                         SELECT 1 FROM repertoire_cards linked
                         WHERE linked.card_id=c.id AND linked.repertoire_id=eligible.id
                     )) AND NOT EXISTS(
                         SELECT 1 FROM repertoire_integrity_card_blocks block
                         WHERE block.repertoire_id=eligible.id AND block.card_id=c.id
                     )
                 )
           )""",
        (day, day),
    )


def _restore_published_study_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='queued'
           WHERE queue_date=? AND status='blocked' AND card_id IN (
             SELECT card.id FROM cards card
             JOIN study_exercises exercise ON exercise.id=card.study_exercise_id
             JOIN studies study ON study.id=exercise.study_id
             WHERE card.content_type='study_exercise' AND card.archived=0
               AND card.pending_validation=0 AND study.archived=0
               AND exercise.status='published' AND card.due_date<=?
               AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                              WHERE burial.exercise_id=exercise.id AND burial.study_day=?)
           )""",
        (day, day, day),
    )


def _reset_unintroduced_opening_cards(database, queue_date: str) -> None:
    database.execute(
        """UPDATE cards SET state='new' WHERE content_type='opening' AND state='learning'
           AND introduced_at IS NULL AND NOT EXISTS(
               SELECT 1 FROM reviews review WHERE review.card_id=cards.id)"""
    )


def _reset_stale_opening_introductions(database, queue_date: str) -> None:
    statement = """UPDATE cards SET state='new',introduced_at=NULL
           WHERE content_type='opening' AND repertoire_id<>'__retained_cards__' AND state='learning' AND introduced_at<?
             AND NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=cards.id)
             AND NOT EXISTS(SELECT 1 FROM daily_queue queue
                            WHERE queue.card_id=cards.id AND queue.queue_date=?)"""
    from tempo_legacy_database.adapters.study.game_feedback import installed, retained_encounter_introduction_query
    if installed(database):
        statement += ' AND cards.id NOT IN (' + retained_encounter_introduction_query() + ')'
        # Keep this owned PostgreSQL projection native: the legacy SQLite
        # translator would rewrite its JSONB operator into a JSON-only function.
        database.execute_native(statement.replace('?', '%s'), (queue_date, queue_date))
        return
    database.execute(statement, (queue_date, queue_date))
