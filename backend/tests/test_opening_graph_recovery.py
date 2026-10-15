"""Maintenance claim guards; end-to-end proof lives in the regular PostgreSQL suite."""
import json
from unittest.mock import Mock
import pytest
from app.services.opening_graph_recovery import claim_exact_task


def task_row(**changes):
    return {'id': 'target', 'kind': 'opening_graph_rebuild', 'deduplication_key': 'caro',
            'generation': 1, 'state': 'failed', 'phase': 'publish',
            'payload_json': json.dumps({'repertoire_id': 'caro'}), **changes}


@pytest.mark.parametrize('changes', [
    {'kind': 'repertoire_priority'}, {'deduplication_key': 'other'},
    {'generation': 2}, {'state': 'leased'}, {'phase': 'stage'},
    {'payload_json': '{"repertoire_id":"other"}'},
])
def test_maintenance_claim_refuses_wrong_scope_generation_lease_or_checkpoint(changes):
    database = Mock()
    database.execute_native.return_value.fetchone.return_value = task_row(**changes)
    with pytest.raises(ValueError):
        claim_exact_task(database, 'target', 'opening_graph_rebuild', 'caro', 1,
                         resume_failed=True, day='2026-10-15')
    assert database.execute_native.call_count == 1


def test_maintenance_preflight_and_release_yield_to_foreground(monkeypatch):
    from app.services import opening_graph_recovery as recovery
    calls = []
    def denied_then_ready():
        calls.append(True)
        if len(calls) == 1:
            raise recovery.BackgroundAdmissionDeferred('Foreground is active')
        return 'ready'
    monkeypatch.setattr(recovery.time, 'sleep', lambda _seconds: None)
    assert recovery.wait_for_admission(denied_then_ready) == 'ready'
    assert len(calls) == 2
