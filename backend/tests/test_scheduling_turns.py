"""Discretionary pipelines retain bounded turns across claims and restarts."""

from datetime import timedelta
import json
from unittest.mock import patch

import pytest

from app import database, tasks
from app.services import durable_tasks
from test_daily_study_dispatch import dispatch_store, task_row


PIPELINE_KINDS = (
    'opening_graph_rebuild', 'game_derivation_positions', 'repertoire_priority',
    'opening_segmentation', 'game_sync_record',
)


def enqueue_pipeline(kind, key, clock, *, priority=100, delay=0):
    queued = durable_tasks.enqueue_task(kind, key, {'cursor': 0}, priority=priority,
                                       delay_seconds=delay, foreground=False)
    clock[0] += timedelta(milliseconds=1)
    return queued


def claim_and_checkpoint(allowed=tasks._SUPPORTED_BACKGROUND_KINDS):
    claimed = durable_tasks.claim_task(allowed_kinds=allowed)
    if claimed:
        with database.connection(background=True) as connection:
            assert durable_tasks.advance_task_slice_in_transaction(
                connection, claimed, next_phase='slice',
                next_payload={'cursor': claimed['payload']['cursor'] + 1})
    return claimed


def test_discretionary_turns_advance_games_amid_continuous_graph_backlog(dispatch_store):
    clock, _ = dispatch_store
    for index, kind in enumerate(PIPELINE_KINDS):
        enqueue_pipeline(kind, kind, clock, priority=40 if index == 0 else 130)
    claimed_kinds = [claim_and_checkpoint()['kind'] for _ in range(14)]
    expected = [PIPELINE_KINDS[index] for index in (0, 1, 0, 1, 2, 3, 4)]
    assert claimed_kinds == expected * 2
    for kind, count in zip(PIPELINE_KINDS, (4, 4, 2, 2, 2)):
        with database.read_connection() as connection:
            row = connection.execute('SELECT payload_json FROM background_tasks WHERE kind=?', (kind,)).fetchone()
            assert json.loads(row[0])['cursor'] == count


def test_scheduling_turn_survives_database_reopen_and_uncommitted_claim(dispatch_store):
    clock, _ = dispatch_store
    for kind in PIPELINE_KINDS:
        enqueue_pipeline(kind, kind, clock)
    assert claim_and_checkpoint()['kind'] == PIPELINE_KINDS[0]
    # Connections reopen for every claim; a failed claim transaction must retain its turn.
    def interrupted(*args, **kwargs):
        raise RuntimeError('controlled claim rollback')
    with patch.object(durable_tasks, '_record_event', interrupted):
        with pytest.raises(RuntimeError, match='claim rollback'):
            claim_and_checkpoint()
    assert claim_and_checkpoint()['kind'] == PIPELINE_KINDS[1]
    assert claim_and_checkpoint()['kind'] == PIPELINE_KINDS[0]


def test_scheduling_skips_paused_disabled_and_intentionally_delayed_classes(dispatch_store):
    clock, _ = dispatch_store
    paused = enqueue_pipeline(PIPELINE_KINDS[0], 'paused', clock)
    enqueue_pipeline(PIPELINE_KINDS[1], 'delayed', clock, delay=60)
    enqueue_pipeline('defensive_threat_scan', 'disabled', clock)
    expected = enqueue_pipeline(PIPELINE_KINDS[2], 'eligible', clock)
    with database.connection(background=True) as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=0 WHERE id=1')
        connection.execute("INSERT INTO background_activity(source,work_id,paused,updated_at) VALUES('durable',?,1,?)",
                           (paused['id'], clock[0].isoformat()))
    assert claim_and_checkpoint()['id'] == expected['id']
    assert task_row(paused['id'])['attempt_count'] == 0
    clock[0] += timedelta(seconds=60)
    assert claim_and_checkpoint()['kind'] == PIPELINE_KINDS[1]


def test_scheduling_selects_oldest_eligible_work_without_priority_starvation(dispatch_store):
    clock, _ = dispatch_store
    oldest = enqueue_pipeline(PIPELINE_KINDS[1], 'oldest', clock, priority=150)
    enqueue_pipeline(PIPELINE_KINDS[1], 'newer', clock, priority=1)
    assert claim_and_checkpoint()['id'] == oldest['id']


def test_promoted_backlog_cannot_consume_other_pipelines_persisted_turns(dispatch_store):
    clock, _ = dispatch_store
    for kind in PIPELINE_KINDS:
        queued = enqueue_pipeline(kind, kind, clock)
        if kind == PIPELINE_KINDS[0]:
            with database.connection(background=True) as connection:
                connection.execute("INSERT INTO background_activity(source,work_id,promoted,updated_at) VALUES('durable',?,1,?)",
                                   (queued['id'], clock[0].isoformat()))
    selected = [claim_and_checkpoint()['kind'] for _ in range(14)]
    assert selected[::2] == [PIPELINE_KINDS[0]] * 7
    assert selected[1::2] == [PIPELINE_KINDS[index] for index in (0, 1, 0, 1, 2, 3, 4)]


def test_prompt_control_backlog_yields_after_two_slices_without_losing_intent(dispatch_store):
    clock, _ = dispatch_store
    enqueue_pipeline('daily_queue', 'study', clock, priority=10)
    enqueue_pipeline(PIPELINE_KINDS[1], 'game', clock, priority=130)
    assert [claim_and_checkpoint()['kind'] for _ in range(9)] == ['daily_queue', 'daily_queue', PIPELINE_KINDS[1]] * 3


def test_scheduling_empty_queue_preserves_turn_and_new_supported_kind_is_not_lost(dispatch_store):
    clock, _ = dispatch_store
    allowed = (*PIPELINE_KINDS, 'new_supported_pipeline')
    assert durable_tasks.claim_task(allowed_kinds=allowed) is None
    with database.read_connection() as connection:
        assert connection.execute("SELECT next_turn FROM background_scheduling_turns WHERE lane='durable'").fetchone()[0] == 0
    queued = enqueue_pipeline('new_supported_pipeline', 'future', clock)
    assert claim_and_checkpoint(allowed)['id'] == queued['id']
