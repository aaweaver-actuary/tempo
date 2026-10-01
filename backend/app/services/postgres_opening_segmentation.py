"""Event-driven, bounded PostgreSQL advisory opening projections."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from .. import postgres_store
from ..database import background_read_connection
from .durable_tasks import (advance_task_slice_in_transaction, complete_task_slice_in_transaction,
                            enqueue_task_in_transaction, lock_current_slice)
from .redis_admission_gate import background_lease
from .opening_segmentation import (candidate_parts, presentation_occurrences, stable_key)

GROUP_PAGE_SIZE = 8


def invalidate_segmentation_in_transaction(database, repertoire_id: str) -> None:
    database.execute_native(
        "INSERT INTO opening_segmentation_state(repertoire_id,content_version,state) VALUES(%s,1,'stale') "
        "ON CONFLICT(repertoire_id) DO UPDATE SET content_version=opening_segmentation_state.content_version+1,state='stale'",
        (repertoire_id,),
    )


def request_segmentation_in_transaction(database, repertoire_id: str, graph_generation: int) -> dict:
    database.execute_native(
        "INSERT INTO opening_segmentation_state(repertoire_id) VALUES(%s) ON CONFLICT DO NOTHING", (repertoire_id,),
    )
    state = database.execute_native(
        "SELECT content_version,run_id,state FROM opening_segmentation_state WHERE repertoire_id=%s FOR UPDATE",
        (repertoire_id,),
    ).fetchone()
    if state[2] in {'ready', 'building'}:
        return {"queued": state[2] == 'building'}
    task = enqueue_task_in_transaction(
        database, "opening_segmentation", repertoire_id,
        {"repertoire_id": repertoire_id, "graph_generation": graph_generation,
         "content_version": int(state[0]), "after_card_id": ""}, priority=60,
    )
    run_id = f"{task['id']}:{task['generation']}"
    database.execute_native("INSERT INTO opening_segmentation_runs(id,repertoire_id) VALUES(%s,%s)", (run_id, repertoire_id))
    database.execute_native(
        "UPDATE opening_segmentation_state SET state='building',run_id=%s,graph_generation=%s WHERE repertoire_id=%s",
        (run_id, graph_generation, repertoire_id),
    )
    return {"queued": True, "task_id": task['id']}


def run_identity(task: dict) -> str:
    return f"{task['id']}:{task['generation']}"


def current_slice(database, task: dict) -> bool:
    if not lock_current_slice(database, task):
        return False
    payload = task["payload"]
    current = database.execute_native(
        "SELECT 1 FROM opening_segmentation_state state "
        "JOIN opening_graph_publications graph ON graph.repertoire_id=state.repertoire_id "
        "WHERE state.repertoire_id=%s AND state.content_version=%s AND state.run_id=%s "
        "AND graph.generation=%s AND state.state='building' FOR UPDATE OF state",
        (payload['repertoire_id'], payload['content_version'], run_identity(task), payload['graph_generation']),
    ).fetchone()
    if current is None:
        complete_task_slice_in_transaction(database, task)
        return False
    return True


def prepare_presentation(task: dict) -> tuple[str, tuple[dict, ...]] | None:
    payload = task['payload']
    with background_read_connection() as database:
        row = database.execute_native(
            "SELECT card.id,card.start_fen,card.moves_json,card.revision,card.trained_color "
            "FROM cards card WHERE card.id>%s AND card.archived=0 AND card.pending_validation=0 "
            "AND EXISTS(SELECT 1 FROM opening_graph_steps step WHERE step.repertoire_id=%s "
            "AND step.generation=%s AND step.card_id=card.id "
            "AND step.starting_fen=card.start_fen AND step.moves_json=card.moves_json) "
            "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.repertoire_id=%s AND block.card_id=card.id) ORDER BY card.id LIMIT 1",
            (payload.get('after_card_id', ''), payload['repertoire_id'], payload['graph_generation'], payload['repertoire_id']),
        ).fetchone()
        presentation = dict(row) if row else None
    if presentation is None:
        return None
    return presentation['id'], presentation_occurrences(payload['repertoire_id'], presentation)


def stage_presentation(database, task: dict, prepared: tuple | None) -> bool:
    if not current_slice(database, task):
        return False
    payload = dict(task['payload'])
    if prepared is None:
        return advance_task_slice_in_transaction(database, task, next_phase='groups',
                                                next_payload={**payload, 'after_kind': '', 'after_group_key': ''})
    card_id, occurrences = prepared
    run_id = run_identity(task)
    with database.raw.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO opening_segmentation_occurrences(run_id,card_id,decision_index,trunk_key,suffix_key,occurrence_json) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [(run_id, card_id, item['decision_index'], item['trunk_key'], item['suffix_key'], json.dumps(item)) for item in occurrences],
        )
        groups = [(run_id, 'transposition', item['suffix_key']) for item in occurrences]
        groups.extend((run_id, 'shared_trunk', item['trunk_key']) for item in occurrences
                      if item['decision_index'] + 1 < item['decision_count'])
        cursor.executemany(
            "INSERT INTO opening_segmentation_groups(run_id,kind,group_key) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", groups,
        )
    return advance_task_slice_in_transaction(database, task, next_phase='scan',
                                            next_payload={**payload, 'after_card_id': card_id})


def prepare_group_page(task: dict) -> tuple[str, str, tuple[dict, ...]] | None:
    payload = task['payload']
    run_id = run_identity(task)
    with background_read_connection() as database:
        if payload.get('group_key'):
            kind, group_key = payload['group_kind'], payload['group_key']
        else:
            group = database.execute_native(
                "SELECT kind,group_key FROM opening_segmentation_groups WHERE run_id=%s "
                "AND (kind,group_key)>(%s,%s) ORDER BY kind,group_key LIMIT 1",
                (run_id, payload.get('after_kind', ''), payload.get('after_group_key', '')),
            ).fetchone()
            if group is None:
                return None
            kind, group_key = group
        key_column = 'trunk_key' if kind == 'shared_trunk' else 'suffix_key'
        rows = database.execute_native(
            f"SELECT occurrence_json FROM opening_segmentation_occurrences WHERE run_id=%s AND {key_column}=%s "
            "AND (card_id,decision_index)>(%s,%s) ORDER BY card_id,decision_index LIMIT %s",
            (run_id, group_key, payload.get('group_after_card', ''), payload.get('group_after_index', -1), GROUP_PAGE_SIZE),
        ).fetchall()
    occurrences = tuple(json.loads(row[0]) for row in rows)
    # Chess normalization and candidate construction happen after the read closes.
    parts = {part['id']: part for item in occurrences for part in candidate_parts(kind, item) if part['tested_decisions']}
    return kind, group_key, occurrences, tuple(parts.values())


def stage_group_page(database, task: dict, prepared: tuple | None) -> bool:
    if not current_slice(database, task):
        return False
    payload = dict(task['payload'])
    if prepared is None:
        return advance_task_slice_in_transaction(database, task, next_phase='cleanup', next_payload={**payload, 'cleanup_table': 0})
    kind, group_key, occurrences, prepared_parts = prepared
    run_id = run_identity(task)
    recommendation_id = stable_key(payload['repertoire_id'], kind, group_key)
    if not occurrences:
        group = database.execute_native(
            'SELECT source_count,diverse,decisions_before,segment_count,decisions_after,source_fingerprint '
            'FROM opening_segmentation_groups WHERE run_id=%s AND kind=%s AND group_key=%s', (run_id, kind, group_key),
        ).fetchone()
        savings = int(group[2]) - int(group[4])
        additional_starts = max(0, int(group[3]) - int(group[0]))
        if group[0] > 1 and group[1] and savings > 0 and savings >= additional_starts:
            database.execute_native(
                "INSERT INTO opening_segmentation_recommendations(run_id,id,repertoire_id,kind,decisions_before,decisions_after,"
                "decisions_avoided,additional_starts,segment_count,source_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (run_id, recommendation_id, payload['repertoire_id'], kind, group[2], group[4], savings, additional_starts, group[3], group[5]),
            )
        next_payload = {key: value for key, value in payload.items() if not key.startswith('group_')}
        return advance_task_slice_in_transaction(database, task, next_phase='groups',
                                                next_payload={**next_payload, 'after_kind': kind, 'after_group_key': group_key})
    source_input = [{'card_id': item['card_id'], 'decision_count': item['decision_count'],
                     'discriminator': stable_key(item['next_context'] if kind == 'shared_trunk' else item['incoming_key'])}
                    for item in occurrences]
    new_sources = database.execute_native(
        'INSERT INTO opening_segmentation_sources(run_id,recommendation_id,card_id,decision_count,discriminator) '
        'SELECT %s,%s,card_id,decision_count,discriminator FROM jsonb_to_recordset(%s::jsonb) '
        'AS source(card_id TEXT,decision_count BIGINT,discriminator TEXT) ON CONFLICT DO NOTHING '
        'RETURNING card_id,decision_count,discriminator', (run_id, recommendation_id, json.dumps(source_input)),
    ).fetchall()
    part_input = [{'segment_id': ('0:' if part['role'] in {'shared_trunk','bridge'} else '1:') + part['id'],
                   'tested_decisions': part['tested_decisions'], 'segment_json': json.dumps(part)} for part in prepared_parts]
    new_parts = database.execute_native(
        'INSERT INTO opening_segmentation_parts(run_id,recommendation_id,segment_id,tested_decisions,segment_json) '
        'SELECT %s,%s,segment_id,tested_decisions,segment_json FROM jsonb_to_recordset(%s::jsonb) '
        'AS part(segment_id TEXT,tested_decisions BIGINT,segment_json TEXT) ON CONFLICT DO NOTHING RETURNING tested_decisions',
        (run_id, recommendation_id, json.dumps(part_input)),
    ).fetchall()
    group = database.execute_native(
        'SELECT first_discriminator,diverse,source_fingerprint FROM opening_segmentation_groups '
        'WHERE run_id=%s AND kind=%s AND group_key=%s', (run_id, kind, group_key),
    ).fetchone()
    first_discriminator, diverse, fingerprint = group
    for source in sorted(new_sources, key=lambda item: item[0]):
        fingerprint = stable_key(fingerprint, source[0])
        first_discriminator = first_discriminator or source[2]
        diverse = diverse or source[2] != first_discriminator
    database.execute_native(
        'UPDATE opening_segmentation_groups SET source_count=source_count+%s,decisions_before=decisions_before+%s,'
        'segment_count=segment_count+%s,decisions_after=decisions_after+%s,first_discriminator=%s,diverse=%s,source_fingerprint=%s '
        'WHERE run_id=%s AND kind=%s AND group_key=%s',
        (len(new_sources), sum(source[1] for source in new_sources), len(new_parts), sum(part[0] for part in new_parts),
         first_discriminator, diverse, fingerprint, run_id, kind, group_key),
    )
    final_occurrence = occurrences[-1]
    return advance_task_slice_in_transaction(database, task, next_phase='groups', next_payload={
        **payload, 'group_kind': kind, 'group_key': group_key,
        'group_after_card': final_occurrence['card_id'], 'group_after_index': final_occurrence['decision_index'],
    })


def publish_recommendations(database, task: dict) -> bool:
    if not current_slice(database, task):
        return False
    database.execute_native(
        "UPDATE opening_segmentation_state SET state='ready',published_at=%s WHERE repertoire_id=%s",
        (datetime.now(timezone.utc).isoformat(), task['payload']['repertoire_id']),
    )
    if not complete_task_slice_in_transaction(database, task):
        raise RuntimeError('Segmentation lease changed during publication')
    return False


_RETENTION_TABLES = ('opening_segmentation_occurrences', 'opening_segmentation_groups',
                     'opening_segmentation_parts', 'opening_segmentation_sources',
                     'opening_segmentation_recommendations')


def cleanup_previous_runs(database, task: dict) -> bool:
    if not current_slice(database, task):
        return False
    payload = dict(task['payload'])
    table_index = int(payload.get('cleanup_table', 0))
    if table_index == len(_RETENTION_TABLES):
        # All old projection rows have already been removed in bounded slices.
        database.execute_native(
            'DELETE FROM opening_segmentation_runs WHERE id IN(SELECT id FROM opening_segmentation_runs '
            'WHERE repertoire_id=%s AND id<>%s ORDER BY id LIMIT 8)',
            (payload['repertoire_id'], run_identity(task)),
        )
        remaining = database.execute_native('SELECT 1 FROM opening_segmentation_runs WHERE repertoire_id=%s AND id<>%s LIMIT 1',
                                            (payload['repertoire_id'], run_identity(task))).fetchone()
        return advance_task_slice_in_transaction(database, task, next_phase='cleanup' if remaining else 'publish', next_payload=payload)
    table = _RETENTION_TABLES[table_index]
    removed = database.execute_native(
        f'DELETE FROM {table} WHERE ctid IN(SELECT ctid FROM {table} WHERE run_id>=%s AND run_id<%s '
        'AND run_id<>%s ORDER BY run_id LIMIT 8)',
        (task['id'] + ':', task['id'] + ';', run_identity(task)),
    ).rowcount
    return advance_task_slice_in_transaction(database, task, next_phase='cleanup',
        next_payload={**payload, 'cleanup_table': table_index if removed else table_index + 1})


def execute_segmentation_slice(task: dict[str, Any]) -> bool:
    phase = task.get('phase', 'queued')
    if phase in {'queued', 'scan'}:
        prepared = prepare_presentation(task)
        handler = stage_presentation
    elif phase == 'groups':
        prepared = prepare_group_page(task)
        handler = stage_group_page
    elif phase in {'publish', 'cleanup'}:
        prepared = None
        handler = None
    else:
        raise ValueError(f'Unknown segmentation phase: {phase}')
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            return handler(database, task, prepared) if handler else (cleanup_previous_runs(database, task) if phase == 'cleanup' else publish_recommendations(database, task))
