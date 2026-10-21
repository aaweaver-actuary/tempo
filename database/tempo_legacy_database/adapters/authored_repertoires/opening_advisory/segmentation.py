"""Existing segmentation advisory projections and preference writes."""

from ...prefix_reads.diagnostics import postgres_is_configured
from ..discovery_commands import repertoire


def consistent_preview_read(database):
    database.execute_native('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')


def lock_state(database, repertoire_id):
    database.execute_native('SELECT repertoire_id FROM opening_segmentation_state WHERE repertoire_id=%s FOR UPDATE', (repertoire_id,))


def upsert_preference(database, values):
    database.execute_native(
        'INSERT INTO opening_segmentation_preferences(repertoire_id,recommendation_id,choice,source_fingerprint,updated_at) '
        'VALUES(%s,%s,%s,%s,%s) ON CONFLICT(repertoire_id,recommendation_id) DO UPDATE SET '
        'choice=excluded.choice,source_fingerprint=excluded.source_fingerprint,updated_at=excluded.updated_at',
        values,
    )


def invalidate_failed_build(database, repertoire_id):
    database.execute_native(
        "UPDATE opening_segmentation_state SET state='stale' WHERE repertoire_id=%s AND state='building' "
        "AND EXISTS(SELECT 1 FROM background_tasks WHERE kind='opening_segmentation' AND deduplication_key=%s AND state='failed')",
        (repertoire_id, repertoire_id),
    )


def state_row(database, repertoire_id):
    return database.execute_native(
        "SELECT state.*,task.state task_state,task.last_error FROM opening_segmentation_state state "
        "LEFT JOIN background_tasks task ON task.kind='opening_segmentation' AND task.deduplication_key=state.repertoire_id "
        "WHERE state.repertoire_id=%s", (repertoire_id,),
    ).fetchone()


def recommendation(database, run_id, recommendation_id):
    return database.execute_native(
        'SELECT * FROM opening_segmentation_recommendations WHERE run_id=%s AND id=%s',
        (run_id, recommendation_id),
    ).fetchone()


def prepared_graph(database, repertoire_id):
    return database.execute_native(
        "SELECT graph.generation FROM opening_graph_publications graph JOIN repertoire_integrity_state integrity "
        "ON integrity.repertoire_id=graph.repertoire_id WHERE graph.repertoire_id=%s AND integrity.scan_status='idle' "
        "AND integrity.status IN ('clean','needs_repair')", (repertoire_id,),
    ).fetchone()


def source_fingerprint(database, run_id, recommendation_id):
    return database.execute_native(
        "SELECT source_fingerprint FROM opening_segmentation_recommendations WHERE run_id=%s AND id=%s", (run_id, recommendation_id),
    ).fetchone()[0]


def segment_rows(database, run_id, recommendation_id, after):
    return database.execute_native(
        'SELECT segment_id,segment_json FROM opening_segmentation_parts WHERE run_id=%s AND recommendation_id=%s '
        'AND segment_id>%s ORDER BY segment_id LIMIT 9', (run_id, recommendation_id, after),
    ).fetchall()


def source_route_rows(database, repertoire_id, graph_generation, run_id, recommendation_id, after):
    return database.execute_native(
        "SELECT DISTINCT line.id,line.name FROM opening_segmentation_sources source "
        "JOIN opening_graph_steps step ON step.card_id=source.card_id AND step.repertoire_id=%s AND step.generation=%s "
        "JOIN repertoire_lines line ON line.id=step.line_id "
        "WHERE source.run_id=%s AND source.recommendation_id=%s AND line.id>%s ORDER BY line.id LIMIT 9",
        (repertoire_id, graph_generation, run_id, recommendation_id, after),
    ).fetchall()


def visible_recommendations(database, run_id):
    return database.execute_native(
        "SELECT recommendation.* FROM opening_segmentation_recommendations recommendation "
        "LEFT JOIN opening_segmentation_preferences preference ON preference.repertoire_id=recommendation.repertoire_id "
        "AND preference.recommendation_id=recommendation.id "
        "AND preference.source_fingerprint=recommendation.source_fingerprint "
        "WHERE recommendation.run_id=%s AND preference.recommendation_id IS NULL "
        "ORDER BY decisions_avoided DESC,additional_starts,segment_count,id LIMIT 20", (run_id,),
    ).fetchall()


def invalidated_pin_count(database, repertoire_id, run_id):
    return database.execute_native(
        "SELECT COUNT(*) FROM opening_segmentation_preferences preference WHERE preference.repertoire_id=%s "
        "AND preference.choice='keep_current' AND NOT EXISTS(SELECT 1 FROM opening_segmentation_recommendations recommendation "
        "WHERE recommendation.run_id=%s AND recommendation.id=preference.recommendation_id "
        "AND recommendation.source_fingerprint=preference.source_fingerprint)",
        (repertoire_id, run_id),
    ).fetchone()[0]
