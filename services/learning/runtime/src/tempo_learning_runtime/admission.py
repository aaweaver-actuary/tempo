"""One Learning admission writer; deterministic published route order from #235.

Preparation suggests one card. This writer rechecks active definitions, exact
prerequisites, quota, deduplication and frozen identity under card/queue locks.
"""

import json
from .clock import date
from tempo_learning_database.adapters.queue_position_lock import lock_queue_date_for_position
from . import frozen_opening_order
from .real_game_feedback import MISS_REASON, has_outstanding_real_game_miss


def lock_card(database, card_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("tempo:card-edit:" + card_id,),
    )
    return database.execute(
        "SELECT * FROM cards WHERE id=? FOR UPDATE", (card_id,)
    ).fetchone()


def protected_queue_ids(database, queue_day):
    return {
        row[0]
        for row in database.execute(
            """SELECT q.id FROM daily_queue q JOIN cards card ON card.id=q.card_id
           WHERE q.queue_date=? AND q.status='queued' AND
             (q.admission_kind='explicit' OR q.attempt_failed<>0
              OR q.attempt_state NOT IN ('clean','unstarted','gameplay','reinforcement')
              OR q.review_result_json IS NOT NULL
              OR EXISTS(SELECT 1 FROM queue_attempt_origins origin
                  WHERE origin.queue_entry_id=q.id AND origin.card_id=q.card_id
                    AND origin.revision=card.revision AND origin.issued_as_head=1)
              OR EXISTS(SELECT 1 FROM opening_evidence_attempts attempt
                  WHERE attempt.queue_entry_id=q.id))""",
            (queue_day,),
        ).fetchall()
    }


def admit(database, card_id, queue_day, reason=None):
    lock_card(database, card_id)
    lock_queue_date_for_position(database, queue_day)
    return admit_prelocked(database, card_id, queue_day, reason)


def admit_prelocked(database, card_id, queue_day, reason=None, *, proposal=None):
    """The sole writer after encompassing card/queue locks; no lock callbacks."""
    card = database.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if (
        not card
        or not card["serving_active"]
        or card["archived"]
        or card["superseded_by"]
        or card["pending_validation"]
    ):
        return False
    if database.execute(
        "SELECT 1 FROM deleted_cards WHERE card_id=?", (card_id,)
    ).fetchone():
        return False
    if reason == MISS_REASON:
        if not has_outstanding_real_game_miss(database, card_id):
            return False
        if proposal and (
            card["revision"],
            card["publication_id"],
            card["publication_generation"],
        ) != (
            proposal.card_revision,
            proposal.publication_id,
            proposal.publication_generation,
        ):
            return False
    if card["due_date"] > queue_day and reason is None:
        return False
    if card["state"] == "locked" and reason != MISS_REASON:
        return False
    if (
        card["content_type"] == "defense"
        and not database.execute(
            "SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1"
        ).fetchone()[0]
    ):
        return False
    if card["content_type"] == "study_exercise":
        eligible = database.execute(
            "SELECT 1 FROM study_exercises e JOIN studies s ON s.id=e.study_id WHERE e.id=? AND e.status='published' AND s.archived=0 AND NOT EXISTS(SELECT 1 FROM study_sibling_burials b WHERE b.exercise_id=e.id AND b.study_day=?)",
            (card["study_exercise_id"], queue_day),
        ).fetchone()
        if not eligible:
            return False
    if card["content_type"] == "opening":
        # Current published occurrences, never scores, provide ordinary introductions.
        route = database.execute(
            """SELECT step.repertoire_id FROM opening_graph_steps step JOIN opening_graph_publications publication
         ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation
         JOIN repertoires repertoire ON repertoire.id=step.repertoire_id JOIN repertoire_lines line ON line.id=step.line_id
         WHERE step.card_id=? AND publication.state='ready' AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks b WHERE b.card_id=step.card_id AND b.repertoire_id=step.repertoire_id)
         AND (?=1 OR step.parent_card_id IS NULL OR EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=step.parent_card_id AND r.source_kind='study' AND r.invalidated_at IS NULL))
         AND (?='' OR step.repertoire_id=?)
         ORDER BY repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index LIMIT 1""",
            (
                card_id,
                int(reason == MISS_REASON),
                proposal.route_id if proposal else "",
                proposal.route_id if proposal else "",
            ),
        ).fetchone()
        if not route:
            return False
        membership = route[0]
        planned = frozen_opening_order.annotate(
            database, [{"card_id": card_id, "repertoire_id": membership}]
        )[0]
        if not frozen_opening_order.proposal_valid(database, planned):
            return False
    else:
        membership = card["repertoire_id"]
    if reason == MISS_REASON:
        if database.execute(
            "SELECT 1 FROM daily_queue WHERE card_id=? AND queue_date=? AND status='buried'",
            (card_id, queue_day),
        ).fetchone():
            return False
        from .review_service import ensure_card_queued_after

        protected = protected_queue_ids(database, queue_day)
        if any(
            row[0] in protected
            for row in database.execute(
                "SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued'",
                (card_id, queue_day),
            ).fetchall()
        ):
            return False

        ensure_card_queued_after(
            database,
            card_id,
            after_cards=0,
            attempt_state="gameplay",
            priority_reason=reason,
            queue_date=queue_day,
            protected_ids=protected,
            preserve_order=False,
        )
        database.execute(
            "UPDATE daily_queue SET admission_repertoire_id=COALESCE(admission_repertoire_id,?) WHERE card_id=? AND queue_date=? AND status='queued'",
            (membership, card_id, queue_day),
        )
        database.execute(
            "UPDATE cards SET introduced_at=COALESCE(introduced_at,?),state=CASE WHEN state IN ('new','locked') THEN 'learning' ELSE state END WHERE id=?",
            (queue_day, card_id),
        )
        return True
    if database.execute(
        "SELECT 1 FROM daily_queue WHERE card_id=? AND queue_date=?",
        (card_id, queue_day),
    ).fetchone():
        return False
    new = card["introduced_at"] is None
    if new:
        policy = database.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if card["content_type"] == "opening":
            limit = database.execute(
                "SELECT COALESCE(new_cards_per_day,?) FROM repertoires WHERE id=?",
                (policy["new_cards_per_day"], membership),
            ).fetchone()[0]
            # Origins retain quota even if today's projection/content eligibility changes.
            used = database.execute(
                "SELECT COUNT(DISTINCT card_id) FROM queue_attempt_origins WHERE queue_date=? AND cycle=0 AND admission_kind='new' AND content_type='opening' AND admission_repertoire_id=?",
                (queue_day, membership),
            ).fetchone()[0]
        else:
            limit = (
                policy["study_new_per_day"]
                if card["content_type"] == "study_exercise"
                else policy["tactic_new_per_day"]
                if card["content_type"] == "tactic"
                else policy["new_cards_per_day"]
            )
            used = database.execute(
                "SELECT COUNT(DISTINCT card_id) FROM queue_attempt_origins WHERE queue_date=? AND cycle=0 AND admission_kind='new' AND content_type=?",
                (queue_day, card["content_type"]),
            ).fetchone()[0]
        if used >= limit and reason != MISS_REASON:
            return False
    maximum = database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
        (queue_day,),
    ).fetchone()[0]
    database.execute(
        "INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind,admission_repertoire_id,gameplay_priority_reason) VALUES(?,?,?,?,?,?,?)",
        (
            queue_day,
            card_id,
            maximum,
            card["content_type"],
            "new" if new else "review",
            membership,
            reason,
        ),
    )
    database.execute(
        "UPDATE cards SET introduced_at=COALESCE(introduced_at,?),state=CASE WHEN state='new' THEN 'learning' ELSE state END WHERE id=?",
        (queue_day, card_id),
    )
    return True


def next_candidates(database, day, cursor):
    # The #235 route tuple is retained, with frozen preference ahead of it.
    return [
        dict(row)
        for row in database.execute_native(
            """WITH candidates AS (
      SELECT c.id,ARRAY[
        CASE WHEN c.introduced_at IS NOT NULL THEN '0' WHEN c.content_type='opening' THEN '1' ELSE '2' END,
        LPAD(COALESCE(rank.ordinal,9223372036854775807)::text,20,'0'),
        COALESCE(repertoire.created_at,''),COALESCE(repertoire.id,''),
        COALESCE(line.created_at,''),COALESCE(line.id,''),
        LPAD(COALESCE(step.decision_index,0)::text,20,'0'),c.id] AS sort_key
      FROM cards c LEFT JOIN opening_graph_steps step ON step.card_id=c.id AND step.generation=c.publication_generation
      LEFT JOIN repertoires repertoire ON repertoire.id=step.repertoire_id
      LEFT JOIN repertoire_lines line ON line.id=step.line_id
      LEFT JOIN frozen_opening_activation active ON active.id=1
      LEFT JOIN frozen_opening_ranks rank ON rank.run_id=active.run_id AND rank.card_id=c.id
      WHERE c.serving_active=1 AND c.archived=0 AND c.due_date<=%s)
      SELECT id,sort_key FROM candidates WHERE sort_key>%s::text[] ORDER BY sort_key LIMIT 16""",
            (day, cursor),
        ).fetchall()
    ]
