"""Existing segmentation projection/retention operations; caller owns transactions."""

from ... import postgres_store


def invalidate(database, repertoire_id):
    database.execute_native(
        "INSERT INTO opening_segmentation_state(repertoire_id,content_version,state) VALUES(%s,1,'stale') "
        "ON CONFLICT(repertoire_id) DO UPDATE SET content_version=opening_segmentation_state.content_version+1,state='stale'",
        (repertoire_id,),
    )


def ensure_state(database, repertoire_id):
    database.execute_native(
        "INSERT INTO opening_segmentation_state(repertoire_id) VALUES(%s) ON CONFLICT DO NOTHING", (repertoire_id,),
    )


def insert_run(database, run_id, repertoire_id):
    database.execute_native("INSERT INTO opening_segmentation_runs(id,repertoire_id) VALUES(%s,%s)", (run_id, repertoire_id))


def mark_building(database, run_id, graph_generation, repertoire_id):
    database.execute_native(
        "UPDATE opening_segmentation_state SET state='building',run_id=%s,graph_generation=%s WHERE repertoire_id=%s",
        (run_id, graph_generation, repertoire_id),
    )


def update_group(database, values):
    database.execute_native(
        'UPDATE opening_segmentation_groups SET source_count=source_count+%s,decisions_before=decisions_before+%s,'
        'segment_count=segment_count+%s,decisions_after=decisions_after+%s,first_discriminator=%s,diverse=%s,source_fingerprint=%s '
        'WHERE run_id=%s AND kind=%s AND group_key=%s',
        values,
    )


def mark_ready(database, values):
    database.execute_native(
        "UPDATE opening_segmentation_state SET state='ready',published_at=%s WHERE repertoire_id=%s",
        values,
    )


def insert_occurrences(cursor, values):
    cursor.executemany(
        "INSERT INTO opening_segmentation_occurrences(run_id,card_id,decision_index,trunk_key,suffix_key,occurrence_json) "
        "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        values,
    )


def insert_groups(cursor, groups):
    cursor.executemany(
        "INSERT INTO opening_segmentation_groups(run_id,kind,group_key) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", groups,
    )


def delete_old_runs(database, values):
    database.execute_native(
        'DELETE FROM opening_segmentation_runs WHERE id IN(SELECT id FROM opening_segmentation_runs '
        'WHERE repertoire_id=%s AND id<>%s ORDER BY id LIMIT 8)',
        values,
    )


def delete_old_projection(database, table_index, values):
    table = _RETENTION_TABLES[table_index]
    return database.execute_native(
        f'DELETE FROM {table} WHERE ctid IN(SELECT ctid FROM {table} WHERE run_id>=%s AND run_id<%s '
        'AND run_id<>%s ORDER BY run_id LIMIT 8)',
        values,
    ).rowcount


def locked_state(database, repertoire_id):
    return database.execute_native(
        "SELECT content_version,run_id,state FROM opening_segmentation_state WHERE repertoire_id=%s FOR UPDATE",
        (repertoire_id,),
    ).fetchone()


def current_publication(database, values):
    return database.execute_native(
        "SELECT 1 FROM opening_segmentation_state state "
        "JOIN opening_graph_publications graph ON graph.repertoire_id=state.repertoire_id "
        "WHERE state.repertoire_id=%s AND state.content_version=%s AND state.run_id=%s "
        "AND graph.generation=%s AND state.state='building' FOR UPDATE OF state",
        values,
    ).fetchone()


def insert_recommendation(database, values):
    database.execute_native(
        "INSERT INTO opening_segmentation_recommendations(run_id,id,repertoire_id,kind,decisions_before,decisions_after,"
        "decisions_avoided,additional_starts,segment_count,source_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        values,
    )


def insert_sources(database, values):
    return database.execute_native(
        'INSERT INTO opening_segmentation_sources(run_id,recommendation_id,card_id,decision_count,discriminator) '
        'SELECT %s,%s,card_id,decision_count,discriminator FROM jsonb_to_recordset(%s::jsonb) '
        'AS source(card_id TEXT,decision_count BIGINT,discriminator TEXT) ON CONFLICT DO NOTHING '
        'RETURNING card_id,decision_count,discriminator', values,
    ).fetchall()


def insert_parts(database, values):
    return database.execute_native(
        'INSERT INTO opening_segmentation_parts(run_id,recommendation_id,segment_id,tested_decisions,segment_json) '
        'SELECT %s,%s,segment_id,tested_decisions,segment_json FROM jsonb_to_recordset(%s::jsonb) '
        'AS part(segment_id TEXT,tested_decisions BIGINT,segment_json TEXT) ON CONFLICT DO NOTHING RETURNING tested_decisions',
        values,
    ).fetchall()


def group_fingerprint(database, run_id, kind, group_key):
    return database.execute_native(
        'SELECT first_discriminator,diverse,source_fingerprint FROM opening_segmentation_groups '
        'WHERE run_id=%s AND kind=%s AND group_key=%s', (run_id, kind, group_key),
    ).fetchone()


def next_presentation(database, values):
    return database.execute_native(
        "SELECT card.id,card.start_fen,card.moves_json,card.revision,card.trained_color "
        "FROM cards card WHERE card.id>%s AND card.archived=0 AND card.pending_validation=0 "
        "AND EXISTS(SELECT 1 FROM opening_graph_steps step WHERE step.repertoire_id=%s "
        "AND step.generation=%s AND step.card_id=card.id "
        "AND step.starting_fen=card.start_fen AND step.moves_json=card.moves_json) "
        "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
        "WHERE block.repertoire_id=%s AND block.card_id=card.id) ORDER BY card.id LIMIT 1",
        values,
    ).fetchone()


def occurrence_rows(database, run_id, kind, group_key, payload, page_size):
    key_column = 'trunk_key' if kind == 'shared_trunk' else 'suffix_key'
    return database.execute_native(
        f"SELECT occurrence_json FROM opening_segmentation_occurrences WHERE run_id=%s AND {key_column}=%s "
        "AND (card_id,decision_index)>(%s,%s) ORDER BY card_id,decision_index LIMIT %s",
        (run_id, group_key, payload.get('group_after_card', ''), payload.get('group_after_index', -1), page_size),
    ).fetchall()


def group_summary(database, run_id, kind, group_key):
    return database.execute_native(
        'SELECT source_count,diverse,decisions_before,segment_count,decisions_after,source_fingerprint '
        'FROM opening_segmentation_groups WHERE run_id=%s AND kind=%s AND group_key=%s', (run_id, kind, group_key),
    ).fetchone()


def remaining_old_runs(database, values):
    return database.execute_native('SELECT 1 FROM opening_segmentation_runs WHERE repertoire_id=%s AND id<>%s LIMIT 1',
        values).fetchone()


def next_group(database, values):
    return database.execute_native(
        "SELECT kind,group_key FROM opening_segmentation_groups WHERE run_id=%s "
        "AND (kind,group_key)>(%s,%s) ORDER BY kind,group_key LIMIT 1",
        values,
    ).fetchone()


_RETENTION_TABLES = ('opening_segmentation_occurrences', 'opening_segmentation_groups',
                     'opening_segmentation_parts', 'opening_segmentation_sources',
                     'opening_segmentation_recommendations')


def presentation_cursor(database):
    return database.raw.cursor()


def writer_connection():
    return postgres_store.connection(read_only=False, background=True)
