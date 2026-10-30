"""Safe inner-stage evidence; native fixtures never target existing installations."""
from dataclasses import dataclass
import json
import logging

import pytest

from app.services import feedback_diagnostics as diagnostics
from tempo_learning_remediation import RemediationError


def records(caplog):
    return [json.loads(item.message.removeprefix('study_feedback_diagnostic '))
            for item in caplog.records if item.name == 'tempo.study.feedback']


def test_feedback_trace_retains_safe_inner_reason_without_payload_or_result(caplog):
    caplog.set_level(logging.INFO, logger='tempo.study.feedback')
    @dataclass
    class OpaquePreparedOwner:
        capture: str = 'private PGN credentials'
    result = OpaquePreparedOwner()
    @diagnostics.diagnose_feedback
    def prepare():
        diagnostics.feedback_work('private-operation-id', 7)
        for _ in range(2):
            with diagnostics.feedback_span('publication_projection'):
                pass
        try:
            with diagnostics.feedback_span('target_capture'):
                raise RemediationError('terminal_failure', 'legacy_card_not_introduced')
        except RemediationError:
            pass
        return result
    assert prepare() is result
    record, = records(caplog)
    assert record['failure_stage'] == 'target_capture'
    assert record['error_class'] == 'RemediationError'
    assert record['error_reason'] == 'legacy_card_not_introduced'
    assert record['error_status'] == 'terminal_failure'
    assert record['spans']['publication_projection']['count'] == 2
    assert record['spans']['target_capture']['count'] == 1
    assert record['work_version'] == 7 and len(record['work_token']) == 16
    assert 'private' not in json.dumps(record) and 'result' not in record
    assert diagnostics._current.get() is None


def test_feedback_trace_redacts_unrecognized_reason_status_and_sqlstate(caplog):
    caplog.set_level(logging.INFO, logger='tempo.study.feedback')
    error = RemediationError('private_status', 'private_game_id')
    error.sqlstate = 'private_sql'
    @diagnostics.diagnose_feedback
    def prepare():
        with diagnostics.feedback_span('current_owner_validation'):
            raise error
    with pytest.raises(RemediationError) as caught:
        prepare()
    assert caught.value is error
    record, = records(caplog)
    assert record['failure_stage'] == 'current_owner_validation'
    assert record['sqlstate'] is None
    assert 'error_reason' not in record and 'error_status' not in record
    assert 'private' not in json.dumps(record)


def test_nested_preparation_trace_keeps_one_sweep_and_restores_existing_timing(caplog):
    caplog.set_level(logging.INFO, logger='tempo.study.feedback')
    existing = {'owner': 'outer_command'}
    token = diagnostics._training_timing.set(existing)
    @diagnostics.diagnose_feedback
    def inner():
        with diagnostics.feedback_span('publication_read'):
            return True
    @diagnostics.diagnose_feedback
    def sweep():
        diagnostics.feedback_stage('capture')
        return inner()
    try:
        assert sweep() is True
        assert diagnostics._training_timing.get() is existing
    finally:
        diagnostics._training_timing.reset(token)
    record, = records(caplog)
    assert record['operation'] == 'sweep' and record['result'] is True
    assert record['spans']['publication_read']['count'] == 1
    assert record['spans']['inner']['count'] == 1


def test_preparation_sql_remains_in_outer_command_total_and_reports_local_delta(caplog):
    caplog.set_level(logging.INFO, logger='tempo.study.feedback')
    outer = {'sql_count': 2, 'sql_ms': 3.5, 'connections': [{'read_only': True}]}
    token = diagnostics._training_timing.set(outer)
    @diagnostics.diagnose_feedback
    def prepare():
        timing = diagnostics._training_timing.get()
        timing['sql_count'] += 5
        timing['sql_ms'] += 7.5
        timing['connections'].append({'read_only': True, 'committed': True})
        return True
    try:
        assert prepare() is True
        assert diagnostics._training_timing.get() is outer
        assert outer['sql_count'] == 7 and outer['sql_ms'] == 11
        assert len(outer['connections']) == 2
    finally:
        diagnostics._training_timing.reset(token)
    record, = records(caplog)
    assert record['sql_count'] == 5 and record['sql_ms'] == 7.5
    assert record['connections'] == [{'read_only': True, 'committed': True}]


def test_native_preparation_trace_localizes_capture_failure_and_retains_both_owners(
        native_handoff, monkeypatch, caplog):
    """Injected owner failure after real full-scope publication qualification."""
    from app.product_composition import provider_priority as composition
    caplog.set_level(logging.INFO, logger='tempo.study.feedback')
    case = native_handoff
    before = case.case.readback()
    starts = []
    def unavailable(*args):
        starts.append(args)
        raise RemediationError('terminal_failure', 'legacy_card_not_introduced')
    prepared = composition.prepare_delivery_feedback(case.original,
        connections=case.connections, clock=case.clock, admitted=lambda: True,
        capture=unavailable)
    assert len(starts) == 1 and prepared.provider_feedback is None
    assert prepared.delivery.qualification_reason == 'first_departure_preparation_unavailable'
    assert case.store.delivery(case.original.operation_id) == case.original
    assert case.case.readback() == before
    record, = records(caplog)
    assert record['failure_stage'] == 'target_capture'
    assert record['error_reason'] == 'legacy_card_not_introduced'
    assert record['spans']['publication_read']['count'] == 1
    assert record['spans']['publication_projection']['count'] == 1
    assert record['spans']['first_boundary_qualification']['count'] == 1
    assert 'current_owner_validation' not in record['spans']


# Reuse the current source/native owner fixture and its exact ownership cleanup.
from test_product_delivery_qualification import native_handoff, owned_postgres
