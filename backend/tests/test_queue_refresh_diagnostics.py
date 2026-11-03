"""The task diagnostics expose bounded queue progress, not task payloads."""
import json

from app.services.durable_tasks import annotate_queue_projection_workers, serialize_task


def _row(**overrides):
    row = dict(id='task', kind='daily_queue', deduplication_key='current', generation=4,
        priority=10, state='retrying', phase='queued', attempt_count=1, max_attempts=5,
        next_attempt_at='2026-11-03T12:01:00+00:00', created_at='2026-11-03T12:00:00+00:00',
        updated_at='2026-11-03T12:00:30+00:00', last_error='transaction timed out',
        transaction_timeout_count=8, payload_json=json.dumps({'queue_date': '2026-11-03',
            '_queue_phase': 'study_opening_item', 'study_opening_index': 3, 'secret': 'private-pgn'}),
        transaction_timeout_checkpoint=json.dumps([4, 'queued', {'_queue_phase': 'study_opening_item',
            'study_opening_index': 3, 'secret': 'private-pgn'}]))
    row.update(overrides)
    return row


def test_queue_timeout_diagnostics_expose_only_whitelisted_checkpoint():
    for count in (1, 8, 25):
        result = serialize_task(_row(transaction_timeout_count=count))
        assert result['queue_date'] == '2026-11-03'
        assert result['queue_phase'] == 'study_opening_item'
        assert result['transaction_timeout_count'] == count
        assert result['timeout_checkpoint'] == {'queue_phase': 'study_opening_item', 'study_opening_index': 3}
        assert result['next_retry'] == '2026-11-03T12:01:00+00:00'
        assert result['last_error'] == 'transaction timed out'
        assert 'private-pgn' not in json.dumps(result)
        assert 'payload_json' not in result


def test_queue_diagnostics_reject_unknown_phase_and_private_cursor():
    row = _row(payload_json=json.dumps({'queue_date': '2026-11-03', '_queue_phase': 'private-pgn'}),
        transaction_timeout_checkpoint=json.dumps(['queued', {'_queue_phase': 'private-pgn', 'after_card_id': 'private-pgn'}]))
    result = serialize_task(row)
    assert result['queue_phase'] is None
    assert result['timeout_checkpoint'] == {'queue_phase': None}
    assert 'private-pgn' not in json.dumps(result)


def test_projection_status_distinguishes_active_current_historical_and_failed():
    projections = [{'queue_date': '2026-11-02', 'state': 'refreshing'},
                   {'queue_date': '2026-11-03', 'state': 'refreshing'}]
    active = serialize_task(_row())
    result = annotate_queue_projection_workers(projections, [active], '2026-11-03')
    assert [p['worker_status'] for p in result] == ['historical', 'current']
    assert projections == [{'queue_date': '2026-11-02', 'state': 'refreshing'},
                           {'queue_date': '2026-11-03', 'state': 'refreshing'}]
    failed = serialize_task(_row(state='failed'))
    assert annotate_queue_projection_workers(projections, [failed], '2026-11-03')[1]['worker_status'] == 'inactive'
    wrong_day = serialize_task(_row(payload_json=json.dumps({'queue_date': '2026-11-02'})))
    assert annotate_queue_projection_workers(projections, [wrong_day], '2026-11-03')[1]['worker_status'] == 'inactive'
