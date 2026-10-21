"""Complete existing bounded, native-typed transition state capture."""
import psycopg
from .. import postgres_store

BUSY_ERRORS = (psycopg.errors.QueryCanceled, psycopg.errors.ReadOnlySqlTransaction)
UNSUPPORTED_SOURCE_ERROR = psycopg.errors.InvalidTextRepresentation


def membership_connection():
    return postgres_store.connection(read_only=True, background=True, repeatable_read=True)


def membership_rows(database, identifier, limit):
    return database.execute_native(
            'SELECT card_id FROM repertoire_cards WHERE repertoire_id=%s ORDER BY card_id LIMIT %s',
            (identifier, limit)).fetchall()


def capture_transition_tables(database, identifier, lookup_card_ids, max_rows, max_bytes, snapshot_error):
    table_queries = []
    def include_table(name, query, parameters):
        table_queries.append((name, query, parameters))

    card_parameters = (list(lookup_card_ids),)
    include_table('cards', 'SELECT * FROM cards WHERE id=ANY(%s)', card_parameters)
    include_table('deleted_cards', 'SELECT * FROM deleted_cards WHERE card_id=ANY(%s)', card_parameters)
    include_table('repertoire_cards', 'SELECT * FROM repertoire_cards WHERE card_id=ANY(%s) OR repertoire_id=%s',
         (*card_parameters, identifier))
    for name in ('card_revisions', 'reviews', 'opening_card_schedule_seeds', 'daily_queue',
                 'queue_attempt_origins', 'review_attempt_receipts', 'opening_evidence_attempts', 'study_attempts'):
        include_table(name, f'SELECT * FROM {name} WHERE card_id=ANY(%s)', card_parameters)
    include_table('review_schedule_snapshots',
         'SELECT snapshot.* FROM review_schedule_snapshots snapshot JOIN reviews review ON review.id=snapshot.review_id WHERE review.card_id=ANY(%s)', card_parameters)
    include_table('opening_evidence_observations',
         'SELECT observation.* FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt ON attempt.attempt_id=observation.attempt_id WHERE attempt.card_id=ANY(%s)', card_parameters)
    include_table('opening_evidence_presentations', 'SELECT * FROM opening_evidence_presentations WHERE card_id=ANY(%s)', card_parameters)
    include_table('opening_evidence_queue_contexts',
         'SELECT context.* FROM opening_evidence_queue_contexts context JOIN opening_evidence_presentations presentation '
         'ON presentation.id=context.presentation_snapshot_id WHERE presentation.card_id=ANY(%s)', card_parameters)
    include_table('repertoire_integrity_card_blocks', 'SELECT * FROM repertoire_integrity_card_blocks WHERE card_id=ANY(%s)', card_parameters)
    include_table('pending_commands',
         "SELECT resolved.* FROM (SELECT receipt.*,COALESCE(receipt.payload_json::jsonb->>'card_id',"
         "receipt.payload_json::jsonb#>>'{checkpoint,manifest,card_id}',receipt.payload_json::jsonb#>>'{attempt,card_id}',"
         "attempt.card_id,queue.card_id) card_id FROM operation_receipts receipt "
         "LEFT JOIN study_attempts attempt ON attempt.id=receipt.payload_json::jsonb->>'attempt_id' "
         "LEFT JOIN daily_queue queue ON queue.id::text=receipt.payload_json::jsonb->>'entry_id' "
         "WHERE receipt.state NOT IN ('complete','failed')) resolved WHERE resolved.card_id=ANY(%s)", card_parameters)
    include_table('other_graph_steps',
         'SELECT step.* FROM opening_graph_steps step JOIN opening_graph_publications publication '
         'ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation '
         'WHERE step.card_id=ANY(%s) AND step.repertoire_id<>%s', (*card_parameters, identifier))
    include_table('repertoires',
         'SELECT repertoire.* FROM repertoires repertoire WHERE repertoire.id=%s OR repertoire.id IN '
         '(SELECT repertoire_id FROM repertoire_cards WHERE card_id=ANY(%s)) OR repertoire.id IN '
         '(SELECT repertoire_id FROM cards WHERE id=ANY(%s))', (identifier, *card_parameters, *card_parameters))
    include_table('publications',
         'SELECT publication.*,task.generation task_generation,task.state task_state FROM opening_graph_publications publication '
         "LEFT JOIN background_tasks task ON task.kind='opening_graph_rebuild' AND task.deduplication_key=publication.repertoire_id "
         'WHERE publication.repertoire_id=%s OR publication.repertoire_id IN '
         '(SELECT repertoire_id FROM repertoire_cards WHERE card_id=ANY(%s))', (identifier, *card_parameters))

    # Bind every size to this same MVCC snapshot in one round trip. Keep
    # the cumulative checks in table order, before each native payload,
    # including the original membership-race/error precedence.
    size_queries = []
    for table_index, (_name, query, parameters) in enumerate(table_queries):
        scoped_query = query
        for parameter in parameters:
            scope_column = 'repertoire_id' if parameter == identifier else 'card_ids'
            scope_expression = f'(SELECT {scope_column} FROM snapshot_scope)'
            if scope_column == 'card_ids':
                scope_expression += '::text[]'
            scoped_query = scoped_query.replace('%s', scope_expression, 1)
        size_queries.append(
            f'SELECT {table_index} table_index,COUNT(*) count,'
            f'COALESCE(SUM(octet_length(row_to_json(bounded)::text)),0) bytes FROM ({scoped_query}) bounded')
    sizes = database.execute_native(
        # Bind the potentially large lookup array once rather than sending
        # and planning another copy for every table in this same snapshot.
        'WITH snapshot_scope AS MATERIALIZED (SELECT %s::text[] card_ids,%s::text repertoire_id) '
        'SELECT SUM(count)::bigint count,SUM(bytes) bytes,'
        'array_agg(count ORDER BY table_index) table_counts,'
        'array_agg(bytes ORDER BY table_index) table_bytes FROM ('
        + ' UNION ALL '.join(size_queries) + ') sizes',
        (list(lookup_card_ids), identifier),
    ).fetchone()
    # Only enqueue payloads whose cumulative transfer has passed the same
    # limits. Read/validate them in original order, including stale links
    # before a later limit error. PostgreSQL retains their native types.
    allowed_payload_queries = []
    row_count = byte_count = 0
    for table_index, (_name, query, parameters) in enumerate(table_queries):
        row_count += sizes['table_counts'][table_index]
        byte_count += sizes['table_bytes'][table_index]
        if row_count > max_rows or byte_count > max_bytes:
            break
        allowed_payload_queries.append((query, parameters))
    payload_cursors = database.execute_native_batch(allowed_payload_queries)
    raw_tables = []
    row_count = byte_count = 0
    for table_index, (name, query, parameters) in enumerate(table_queries):
        row_count += sizes['table_counts'][table_index]
        byte_count += sizes['table_bytes'][table_index]
        if row_count > max_rows or byte_count > max_bytes:
            raise snapshot_error('limit_exceeded', 'Transition state exceeds 40,000 rows or 4 MiB. This snapshot needs validated planner capacity beyond the current limits; retain its history and source data.')
        rows = payload_cursors[table_index].fetchall()
        raw_tables.append((name, rows))
        if name == 'repertoire_cards' and any(row['card_id'] not in lookup_card_ids for row in rows):
            raise snapshot_error('stale_snapshot', 'Memberships changed during planning. Refresh and retry.')
    return raw_tables
