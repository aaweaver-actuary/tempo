"""Complete queue HTTP projections and entry writes on the caller transaction."""

from collections.abc import Callable

from tempo_legacy_database.adapters import frozen_order, postgres_store
from . import availability


def issued_head(database, entry_id, card_id, revision):
    return database.execute(
        "SELECT issued_as_head FROM queue_attempt_origins "
            "WHERE queue_entry_id=? AND card_id=? AND revision=?",
        (entry_id, card_id, revision),
    ).fetchone()


def begin_head_issuance(database) -> None:
    database.execute(
        "BEGIN IMMEDIATE"
    )


def evidence_timezone(database):
    return database.execute(
        "SELECT timezone FROM settings WHERE id=1"
    ).fetchone()


def queue_projection(database, day: str):
    return database.execute(
        "SELECT * FROM queue_projections WHERE queue_date=?",
        (day,),
    ).fetchone()


def queue_diagnostics(database, day: str):
    return database.execute(
        "SELECT card_id,message FROM queue_projection_diagnostics WHERE queue_date=? ORDER BY card_id",
        (day,),
    ).fetchall()


def queue_rows(database, day: str, limit: int | None, *, miss_reason: str):
    queue_sql = """SELECT q.id queue_entry_id,q.position,q.cycle,q.attempt_state,q.attempt_failed,
                                  (SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=c.id AND invalidated_at IS NULL) latest_review_id,
                                  q.gameplay_priority_reason,q.admission_kind,
                                  CASE WHEN c.kind='prefix'
                                              AND instr(c.recent_attempts_json,'"again"')>0
                                    THEN COALESCE((SELECT MAX(failed_review.id) FROM reviews failed_review
                                      WHERE failed_review.card_id=c.id AND failed_review.rating='again'
                                        AND failed_review.source_kind='study'
                                        AND failed_review.invalidated_at IS NULL),0)
                                    ELSE 0 END latest_failed_review_id,
                                  EXISTS(SELECT 1 FROM reviews study_review
                                         WHERE study_review.card_id=c.id AND study_review.source_kind='study'
                                           AND study_review.invalidated_at IS NULL) has_study_review,
                                  (SELECT COUNT(*) FROM reviews study_review_count
                                   WHERE study_review_count.card_id=c.id
                                     AND study_review_count.source_kind='study'
                                     AND study_review_count.invalidated_at IS NULL) study_review_count,
                                  COALESCE(q.admission_source,
                                    (SELECT 'defense:' || candidate.id
                                     FROM threat_training_candidates candidate
                                     WHERE candidate.card_id=c.id LIMIT 1)) admission_source,c.*,
                                  COALESCE(r.name,study.title,'Study') repertoire_name,
                                  COALESCE(r.source_name,'Study') repertoire_source,
                                  COALESCE(r.is_main,0) is_main,study.id study_id,
                                  COALESCE(c.trained_color,(SELECT e.trained_color FROM endgame_templates e WHERE e.card_id=c.id), (SELECT l.trained_color FROM repertoire_lines l WHERE l.repertoire_id=r.id ORDER BY l.created_at LIMIT 1)) effective_trained_color
                           FROM daily_queue q JOIN cards c ON c.id=q.card_id
                           LEFT JOIN repertoires r ON r.id=COALESCE(
                               (SELECT linked.id FROM repertoires linked
                                WHERE ((linked.id=c.repertoire_id AND EXISTS(
                                    SELECT 1 FROM repertoire_cards primary_link
                                    WHERE primary_link.repertoire_id=linked.id)) OR EXISTS(
                                    SELECT 1 FROM repertoire_cards rc
                                    WHERE rc.card_id=c.id AND rc.repertoire_id=linked.id))
                                  AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                                 WHERE block.repertoire_id=linked.id AND block.card_id=c.id)
                                ORDER BY linked.is_main DESC,linked.created_at DESC LIMIT 1),
                               c.repertoire_id)
                           LEFT JOIN study_exercises exercise ON exercise.id=c.study_exercise_id
                           LEFT JOIN studies study ON study.id=exercise.study_id
                           WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
                             AND c.superseded_by IS NULL
                             AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)
                             AND (c.content_type!='defense' OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
                             AND COALESCE(c.pending_validation,0)=0
                             AND (c.content_type!='study_exercise' OR
                                  (exercise.status='published' AND study.archived=0
                                   AND (q.admission_kind='explicit' OR NOT EXISTS(
                                     SELECT 1 FROM study_sibling_burials burial
                                     WHERE burial.exercise_id=exercise.id AND burial.study_day=q.queue_date))))
                             AND (c.content_type!='opening' OR EXISTS(
                                 SELECT 1 FROM repertoires repertoire
                                 WHERE (repertoire.id=c.repertoire_id OR EXISTS(
                                     SELECT 1 FROM repertoire_cards link
                                     WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))
                                   AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                       WHERE block.repertoire_id=repertoire.id AND block.card_id=c.id)))
                           ORDER BY q.position,q.id"""
    queue_sql = queue_sql.replace(
        "ORDER BY q.position,q.id",
        frozen_order.stale_queue_predicate(miss_reason) + (frozen_order.legacy_queue_predicate(miss_reason) if postgres_store.configured() else "") + " ORDER BY q.position,q.id",
    )
    if postgres_store.configured():
        # Resolve linked repertoires once as a set. The correlated SQLite
        # lookup makes PostgreSQL scan repertoire_cards thousands of times
        # before it joins today's queue.
        original_lookup = queue_sql[
            queue_sql.index("LEFT JOIN repertoires r ON"):
            queue_sql.index("LEFT JOIN study_exercises exercise")
        ]
        repertoire_lookup = (
            "LEFT JOIN ranked_repertoires choice "
            "ON choice.card_id=c.id AND choice.rank=1 "
            "LEFT JOIN repertoires r ON r.id=COALESCE(choice.repertoire_id,c.repertoire_id) "
        )
        candidate_window = availability.available_attempt_sql(miss_reason).replace("SELECT q.id FROM", "SELECT q.* FROM", 1)
        candidate_window += " ORDER BY q.position,q.id" + (" LIMIT ?" if limit is not None else "")
        queue_sql = (
            "WITH active_queue AS MATERIALIZED "
            "(" + candidate_window + "), "
            "candidate_repertoires AS MATERIALIZED ("
            "SELECT c.id card_id,linked.id repertoire_id,linked.is_main,linked.created_at "
            "FROM active_queue queue_card JOIN cards c ON c.id=queue_card.card_id "
            "JOIN repertoire_cards rc ON rc.card_id=c.id "
            "JOIN repertoires linked ON linked.id=rc.repertoire_id "
            "WHERE NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.repertoire_id=linked.id AND block.card_id=c.id) "
            "UNION SELECT c.id card_id,linked.id repertoire_id,linked.is_main,linked.created_at "
            "FROM active_queue queue_card JOIN cards c ON c.id=queue_card.card_id "
            "JOIN repertoires linked ON linked.id=c.repertoire_id "
            "WHERE EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.repertoire_id=linked.id) "
            "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.repertoire_id=linked.id AND block.card_id=c.id)), "
            "ranked_repertoires AS MATERIALIZED (SELECT card_id,repertoire_id,"
            "ROW_NUMBER() OVER (PARTITION BY card_id ORDER BY is_main DESC,created_at DESC) rank "
            "FROM candidate_repertoires) "
            + queue_sql.replace(original_lookup, repertoire_lookup)
            .replace("FROM daily_queue q JOIN cards c", "FROM active_queue q JOIN cards c")
            .replace("WHERE q.queue_date=? AND q.status='queued' AND c.archived=0", "WHERE c.archived=0")
        )
    return database.execute(
        queue_sql + (" LIMIT ?" if limit is not None else ""),
        (day, limit, limit) if limit is not None and postgres_store.configured() else
        (day, limit) if limit is not None else (day,),
    ).fetchall()


def encounter_rows(database, fen_key_batch, badge_cutoff):
    placeholders = ",".join("?" for _ in fen_key_batch)
    return database.execute(
        f"""SELECT occurrence.fen_key,
                           COUNT(DISTINCT occurrence.game_id) encounter_count,
                           MAX(game.played_at) last_seen_at
                    FROM game_position_occurrences occurrence
                    JOIN imported_games game ON game.id=occurrence.game_id
                    WHERE occurrence.fen_key IN ({placeholders})
                      AND game.played_at>=? AND game.adaptive_excluded=0
                    GROUP BY occurrence.fen_key""",
        (*fen_key_batch, badge_cutoff),
    ).fetchall()


def available_count(database, day: str, *, miss_reason: str):
    return database.execute(
        "SELECT COUNT(*) FROM (" + availability.available_attempt_sql(miss_reason) + ") eligible_queue",
        (day,),
    ).fetchone()[0]


def exercise_revision(database, exercise_id):
    return database.execute(
        """SELECT exercise.current_revision,revision.specification_json
                       FROM study_exercises exercise JOIN study_exercise_revisions revision
                         ON revision.exercise_id=exercise.id AND revision.revision=exercise.current_revision
                       WHERE exercise.id=?""",
        (exercise_id,),
    ).fetchone()


def entry_state(database, entry_id: int):
    return database.execute(
        "SELECT queue_date,status,attempt_failed FROM daily_queue WHERE id=?",
        (entry_id,),
    ).fetchone()


def available_head(database, current_day: Callable[[], str], *, miss_reason: str):
    return database.execute(
        availability.available_attempt_sql(miss_reason) + " ORDER BY q.position,q.id LIMIT 1",
        (current_day(),),
    ).fetchone()


def mark_entry_failed(database, entry_id: int):
    return database.execute(
        """UPDATE daily_queue SET attempt_failed=1 WHERE id=? AND status='queued'
               AND ((SELECT content_type FROM cards WHERE id=card_id)!='defense'
                    OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)""",
        (entry_id,),
    ).rowcount


def bury_entry(database, day: str, entry_id: int) -> None:
    database.execute(
        """UPDATE daily_queue SET status='buried'
               WHERE queue_date=? AND status!='complete'
                 AND card_id=(SELECT card_id FROM daily_queue WHERE id=?)""",
        (day, entry_id),
    )
