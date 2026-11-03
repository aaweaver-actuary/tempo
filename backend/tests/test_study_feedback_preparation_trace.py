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


def test_feedback_trace_aggregates_only_fixed_numeric_preparation_work(caplog):
    caplog.set_level(logging.INFO, logger='tempo.study.feedback')
    work = dict(input_plies=160, prefix_nodes=80, replayed_nodes=80,
        projected_frame_records=81, accounting_batches=1,
        work_limit_plies=100000, accounting_interval_nodes=128,
        private_source='private PGN credentials', elapsed_seconds=float('nan'))
    @diagnostics.diagnose_feedback
    def prepare():
        diagnostics.feedback_preparation_work(work)
        diagnostics.feedback_preparation_work(work | dict(input_plies=40))
        diagnostics.feedback_preparation_work(work | dict(input_plies=True))
        diagnostics.feedback_preparation_work({'private': 'do not serialize'})
        return True
    assert prepare() is True
    record, = records(caplog)
    assert record['preparation_work'] == dict(repertoires=2,
        input_plies=200, max_repertoire_input_plies=160,
        prefix_nodes=160, replayed_nodes=160, projected_frame_records=162,
        accounting_batches=2, work_limit_plies=100000, accounting_interval_nodes=128)
    assert 'private' not in json.dumps(record) and 'NaN' not in json.dumps(record)
    assert 'elapsed_seconds' not in record['preparation_work']


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
    from contextlib import contextmanager
    from app.product_composition import provider_priority as composition
    from app.services import study_priority
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    caplog.set_level(logging.INFO, logger='tempo.study.feedback')
    caplog.set_level(logging.WARNING, logger='tempo.games.priority_intake')
    case = native_handoff
    command = case.original.command
    before = case.case.readback()
    with case.case.transaction() as database:
        study_only = bool(database.execute_native('SELECT study_only_mode FROM settings WHERE id=1').fetchone()[0])
        before_effect = PostgresGameFeedbackStore(database).get(command.key)
    assert before_effect is None and case.original.receipt is None
    # Keep the actual publication/qualification trace. This pure path no longer
    # captures a target; public intake owns the separate capture stage below.
    prepared = composition.prepare_delivery_feedback(case.original,
        connections=case.connections, clock=case.clock, admitted=lambda: True)
    assert prepared.delivery.qualification_status == 'ready'
    assert prepared.provider_feedback is not None
    record, = records(caplog)
    assert record['spans']['publication_read']['count'] == 1
    assert record['spans']['publication_projection']['count'] == 1
    assert record['spans']['first_boundary_qualification']['count'] == 1
    assert record['spans']['current_owner_validation']['count'] == 1
    assert 'failure_stage' not in record

    # Supply the same explicit idle control facts used by the existing optional
    # sync fixtures, retaining this native owner's persisted Study-only setting.
    monkeypatch.setenv('TEMPO_PRODUCT_GAME_SYNC_ENABLED', '1')
    monkeypatch.setattr(study_priority, 'control_snapshot',
        lambda: dict(available=True, active=False, study_only_mode=study_only))
    writers = []
    actual_writer = case.connections.writer
    @contextmanager
    def observe_writer():
        writers.append(True)
        with actual_writer() as database:
            yield database
    monkeypatch.setattr(case.connections, 'writer', observe_writer)
    starts = []
    failure = RemediationError('terminal_failure', 'legacy_card_not_introduced')
    def unavailable(*args):
        starts.append(args)
        raise failure
    intake = composition.CurrentProviderPriorityIntake(
        connections=case.connections, clock=case.clock, operation_stopped=lambda: False,
        capture=unavailable)
    with pytest.raises(RemediationError) as caught:
        intake.accept_encounter(command)
    assert caught.value is failure
    assert starts == [(command.decision.repertoire_id, command.key.game_id,
        (command.key.occurrence_ply - 1,))]
    assert writers == []
    assert case.store.delivery(case.original.operation_id) == case.original
    assert case.case.readback() == before
    with case.case.transaction() as database:
        assert PostgresGameFeedbackStore(database).get(command.key) == before_effect
    capture_record, = [item for item in caplog.records if item.name == 'tempo.games.priority_intake']
    assert capture_record.getMessage() == (
        'Games priority intake deferred stage=capture error=RemediationError code=unclassified')
    # The old reason is deliberately not in this owner's safe logging list.
    # Preserve its redaction and never log commands, game IDs or payloads.
    messages = '\n'.join(item.getMessage() for item in caplog.records
        if item.name in {'tempo.study.feedback', 'tempo.games.priority_intake'})
    assert command.key.game_id not in messages and case.original.operation_id not in messages
    assert 'legacy_card_not_introduced' not in messages


# Reuse the current source/native owner fixture and its exact ownership cleanup.
from test_product_delivery_qualification import native_handoff, owned_postgres
