"""Event-driven, bounded PostgreSQL advisory opening projections."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.authored_repertoires.opening_mutations import segmentation as storage
from tempo_legacy_database.adapters.authored_repertoires.opening_mutations.segmentation import _RETENTION_TABLES
from tempo_legacy_database.connections import background_read_connection
from .durable_tasks import (advance_task_slice_in_transaction, complete_task_slice_in_transaction,
                            enqueue_task_in_transaction, lock_current_slice)
from .redis_admission_gate import background_lease
from .opening_segmentation import (candidate_parts, presentation_occurrences, stable_key)

GROUP_PAGE_SIZE = 8


def invalidate_segmentation_in_transaction(database, repertoire_id: str) -> None:
    storage.invalidate(database, repertoire_id)


def request_segmentation_in_transaction(database, repertoire_id: str, graph_generation: int) -> dict:
    storage.ensure_state(database, repertoire_id)
    state = storage.locked_state(database, repertoire_id)
    if state[2] in {'ready', 'building'}:
        return {"queued": state[2] == 'building'}
    task = enqueue_task_in_transaction(
        database, "opening_segmentation", repertoire_id,
        {"repertoire_id": repertoire_id, "graph_generation": graph_generation,
         "content_version": int(state[0]), "after_card_id": ""}, priority=60,
    )
    run_id = f"{task['id']}:{task['generation']}"
    storage.insert_run(database, run_id, repertoire_id)
    storage.mark_building(database, run_id, graph_generation, repertoire_id)
    return {"queued": True, "task_id": task['id']}


def run_identity(task: dict) -> str:
    return f"{task['id']}:{task['generation']}"


def current_slice(database, task: dict) -> bool:
    if not lock_current_slice(database, task):
        return False
    payload = task["payload"]
    current = storage.current_publication(database, (payload['repertoire_id'], payload['content_version'], run_identity(task), payload['graph_generation']))
    if current is None:
        complete_task_slice_in_transaction(database, task)
        return False
    return True


def prepare_presentation(task: dict) -> tuple[str, tuple[dict, ...]] | None:
    payload = task['payload']
    with background_read_connection() as database:
        row = storage.next_presentation(database, (payload.get('after_card_id', ''), payload['repertoire_id'], payload['graph_generation'], payload['repertoire_id']))
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
    with storage.presentation_cursor(database) as cursor:
        storage.insert_occurrences(cursor, [(run_id, card_id, item['decision_index'], item['trunk_key'], item['suffix_key'], json.dumps(item)) for item in occurrences])
        groups = [(run_id, 'transposition', item['suffix_key']) for item in occurrences]
        groups.extend((run_id, 'shared_trunk', item['trunk_key']) for item in occurrences
                      if item['decision_index'] + 1 < item['decision_count'])
        storage.insert_groups(cursor, groups)
    return advance_task_slice_in_transaction(database, task, next_phase='scan',
                                            next_payload={**payload, 'after_card_id': card_id})


def prepare_group_page(task: dict) -> tuple[str, str, tuple[dict, ...]] | None:
    payload = task['payload']
    run_id = run_identity(task)
    with background_read_connection() as database:
        if payload.get('group_key'):
            kind, group_key = payload['group_kind'], payload['group_key']
        else:
            group = storage.next_group(database, (run_id, payload.get('after_kind', ''), payload.get('after_group_key', '')))
            if group is None:
                return None
            kind, group_key = group
        rows = storage.occurrence_rows(database, run_id, kind, group_key, payload, GROUP_PAGE_SIZE)
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
        group = storage.group_summary(database, run_id, kind, group_key)
        savings = int(group[2]) - int(group[4])
        additional_starts = max(0, int(group[3]) - int(group[0]))
        if group[0] > 1 and group[1] and savings > 0 and savings >= additional_starts:
            storage.insert_recommendation(database, (run_id, recommendation_id, payload['repertoire_id'], kind, group[2], group[4], savings, additional_starts, group[3], group[5]))
        next_payload = {key: value for key, value in payload.items() if not key.startswith('group_')}
        return advance_task_slice_in_transaction(database, task, next_phase='groups',
                                                next_payload={**next_payload, 'after_kind': kind, 'after_group_key': group_key})
    source_input = [{'card_id': item['card_id'], 'decision_count': item['decision_count'],
                     'discriminator': stable_key(item['next_context'] if kind == 'shared_trunk' else item['incoming_key'])}
                    for item in occurrences]
    new_sources = storage.insert_sources(database, (run_id, recommendation_id, json.dumps(source_input)))
    part_input = [{'segment_id': ('0:' if part['role'] in {'shared_trunk','bridge'} else '1:') + part['id'],
                   'tested_decisions': part['tested_decisions'], 'segment_json': json.dumps(part)} for part in prepared_parts]
    new_parts = storage.insert_parts(database, (run_id, recommendation_id, json.dumps(part_input)))
    group = storage.group_fingerprint(database, run_id, kind, group_key)
    first_discriminator, diverse, fingerprint = group
    for source in sorted(new_sources, key=lambda item: item[0]):
        fingerprint = stable_key(fingerprint, source[0])
        first_discriminator = first_discriminator or source[2]
        diverse = diverse or source[2] != first_discriminator
    storage.update_group(database, (len(new_sources), sum(source[1] for source in new_sources), len(new_parts), sum(part[0] for part in new_parts),
         first_discriminator, diverse, fingerprint, run_id, kind, group_key))
    final_occurrence = occurrences[-1]
    return advance_task_slice_in_transaction(database, task, next_phase='groups', next_payload={
        **payload, 'group_kind': kind, 'group_key': group_key,
        'group_after_card': final_occurrence['card_id'], 'group_after_index': final_occurrence['decision_index'],
    })


def publish_recommendations(database, task: dict) -> bool:
    if not current_slice(database, task):
        return False
    storage.mark_ready(database, (datetime.now(timezone.utc).isoformat(), task['payload']['repertoire_id']))
    if not complete_task_slice_in_transaction(database, task):
        raise RuntimeError('Segmentation lease changed during publication')
    return False


def cleanup_previous_runs(database, task: dict) -> bool:
    if not current_slice(database, task):
        return False
    payload = dict(task['payload'])
    table_index = int(payload.get('cleanup_table', 0))
    if table_index == len(_RETENTION_TABLES):
        # All old projection rows have already been removed in bounded slices.
        storage.delete_old_runs(database, (payload['repertoire_id'], run_identity(task)))
        remaining = storage.remaining_old_runs(database, (payload['repertoire_id'], run_identity(task)))
        return advance_task_slice_in_transaction(database, task, next_phase='cleanup' if remaining else 'publish', next_payload=payload)
    removed = storage.delete_old_projection(database, table_index, (task['id'] + ':', task['id'] + ';', run_identity(task)))
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
        with storage.writer_connection() as database:
            return handler(database, task, prepared) if handler else (cleanup_previous_runs(database, task) if phase == 'cleanup' else publish_recommendations(database, task))
