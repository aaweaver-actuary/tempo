"""Existing due/Study eligibility SQL; exported by the legacy composition until its reads move."""

_DUE_CARD_ELIGIBILITY = """c.due_date<=%s AND c.state IN ('learning','mature')
    AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
    AND (c.content_type!='opening' OR EXISTS(
        SELECT 1 FROM repertoires repertoire
        WHERE (repertoire.id=c.repertoire_id OR EXISTS(
            SELECT 1 FROM repertoire_cards link
            WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))
          AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                         WHERE block.repertoire_id=repertoire.id AND block.card_id=c.id)))
    AND (c.content_type!='study_exercise' OR (
        EXISTS(SELECT 1 FROM study_exercises exercise JOIN studies study
               ON study.id=exercise.study_id
               WHERE exercise.id=c.study_exercise_id AND exercise.status='published'
                 AND study.archived=0)
        AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                       WHERE burial.exercise_id=c.study_exercise_id AND burial.study_day=%s)))
    AND NOT EXISTS(SELECT 1 FROM daily_queue queue
                   WHERE queue.card_id=c.id AND queue.queue_date=%s)"""

_STUDY_NEW_ELIGIBILITY = """c.content_type='study_exercise' AND c.state='new'
    AND c.archived=0 AND COALESCE(c.pending_validation,0)=0 AND c.due_date<=%s
    AND exercise.status='published' AND study.archived=0
    AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                   WHERE burial.exercise_id=exercise.id AND burial.study_day=%s)
    AND NOT EXISTS(SELECT 1 FROM daily_queue queue
                   WHERE queue.card_id=c.id AND queue.queue_date=%s)"""
