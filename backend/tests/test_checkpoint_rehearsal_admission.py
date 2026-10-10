"""Checkpoint proofs preserve admission ownership and bounded shared recovery."""

from contextlib import nullcontext
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import command_gateway, tasks
from app.services.redis_admission_gate import BackgroundAdmissionDeferred

rehearsal_specification = importlib.util.spec_from_file_location(
    "checkpoint_admission_rehearsal",
    Path(__file__).resolve().parents[2] / "scripts/check_postgres_opening_evidence.py",
)
rehearsal = importlib.util.module_from_spec(rehearsal_specification)
rehearsal_specification.loader.exec_module(rehearsal)


def prepare_recovery(monkeypatch, claim):
    database = Mock()
    monkeypatch.setattr(rehearsal.postgres_store, "connection", lambda: nullcontext(database))
    monkeypatch.setattr(command_gateway, "claim_recoverable_operation", claim)
    delivery = Mock(return_value={"persisted": True})
    monkeypatch.setattr(tasks.execute_background_command, "run", delivery)
    monkeypatch.setattr(rehearsal.time, "sleep", lambda _: None)
    return database, delivery


def test_checkpoint_recovery_driver_waits_for_foreground_admission(monkeypatch):
    claim = Mock(side_effect=[BackgroundAdmissionDeferred("Waiting for foreground activity"),
                             {"operation_id": "owned", "background": True,
                              "command_name": "opening_evidence.checkpoint", "payload": {"saved": True}}])
    database, delivery = prepare_recovery(monkeypatch, claim)
    assert rehearsal._recover_checkpoint_operation("owned") == {"persisted": True}
    assert claim.call_count == 2
    assert database.execute_native.call_count == 1
    delivery.assert_called_once_with("owned", "opening_evidence.checkpoint", {"saved": True})


def test_checkpoint_recovery_driver_preserves_nonadmission_errors(monkeypatch):
    claim = Mock(side_effect=RuntimeError("database failure"))
    _, delivery = prepare_recovery(monkeypatch, claim)
    with pytest.raises(RuntimeError, match="database failure"):
        rehearsal._recover_checkpoint_operation("owned")
    assert claim.call_count == 1
    delivery.assert_not_called()


def test_checkpoint_receipt_fixture_waits_for_foreground_admission_without_altering_payload(monkeypatch):
    historical_payload = {"checkpoint": {"saved": True}, "prepared_manifest": {"obsolete": True}}
    receipt = Mock(side_effect=[BackgroundAdmissionDeferred("Waiting for foreground activity"),
                               (True, historical_payload, None, None)])
    monkeypatch.setattr(rehearsal.time, "sleep", lambda _: None)
    assert rehearsal._wait_for_checkpoint_admission(
        lambda: receipt("owned", "opening_evidence.checkpoint", historical_payload, background=True)
    ) == (True, historical_payload, None, None)
    assert receipt.call_count == 2
    for invocation in receipt.call_args_list:
        assert invocation.args == ("owned", "opening_evidence.checkpoint", historical_payload)
        assert invocation.kwargs == {"background": True}


def test_checkpoint_recovery_driver_has_bounded_admission_deadline(monkeypatch):
    claim = Mock(side_effect=BackgroundAdmissionDeferred("Waiting for foreground activity"))
    _, delivery = prepare_recovery(monkeypatch, claim)
    clock = iter([0, 11])
    monkeypatch.setattr(rehearsal.time, "monotonic", lambda: next(clock))
    with pytest.raises(AssertionError, match="Recovery never obtained foreground-idle admission"):
        rehearsal._recover_checkpoint_operation("owned")
    assert claim.call_count == 1
    delivery.assert_not_called()


@pytest.fixture
def restart_admission(monkeypatch):
    from app.services import redis_admission_gate
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    parent_keys = redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY
    parent_lease = parent_keys[0], 'persistent-unrelated-parent'

    class AdmissionServer:
        def __init__(self):
            self.leases = {parent_lease: float('inf')}
            self.deleted_keys = []

        def eval(self, script, *arguments):
            if script == redis_admission_gate._REGISTER_FOREGROUND:
                self.leases[(arguments[1], arguments[3])] = float('inf')
            elif script == redis_admission_gate._CLAIM_BACKGROUND:
                if any(key == arguments[1] for key, _token in self.leases):
                    return 0
                self.leases[(arguments[2], arguments[4])] = 1
            return 1

        def zadd(self, key, values):
            self.leases.update({(key, token): score for token, score in values.items()})

        def zscore(self, key, token):
            return self.leases.get((key, token))

        def zrem(self, key, token):
            return self.leases.pop((key, token), None)

        def delete(self, *keys):
            self.deleted_keys.extend(keys)
            self.leases = {identity: score for identity, score in self.leases.items() if identity[0] not in keys}

    server = AdmissionServer()
    monkeypatch.setattr(redis_admission_gate, 'client', lambda: server)
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: True)
    monkeypatch.setattr(redis_admission_gate, 'foreground_present',
                        lambda: any(key == redis_admission_gate._FOREGROUND_KEY for key, _token in server.leases))
    return redis_admission_gate, server, parent_keys, parent_lease


def prepare_restart_proof(monkeypatch, admission, *, fail_stage=None):
    import json
    from contextlib import contextmanager
    from types import SimpleNamespace

    gate, server, parent_keys, parent_lease = admission
    fixture = {'card_id': 'original-card', 'repertoire_id': 'original-repertoire'}
    payload = {'checkpoint': {'attempt_id': 'original-attempt'}, 'prepared_manifest': {'obsolete': True}}
    operation_id = 'original-card-restart'
    result = {'persisted': True, 'contiguous_sequence': 256}
    stages, namespaces = [], []

    def admitted_stage(stage):
        with gate.background_lease():
            stages.append(stage)
            namespaces.append((gate._FOREGROUND_KEY, gate._BACKGROUND_KEY))
            assert server.zscore(*parent_lease) == float('inf')
        if stage == fail_stage:
            with gate.foreground_lease():
                with pytest.raises(BackgroundAdmissionDeferred):
                    with gate.background_lease():
                        pytest.fail('Owned foreground activity must deny background work')
            from psycopg import OperationalError
            raise OperationalError('Controlled ' + stage + ' failure')

    def create_fixture():
        admitted_stage('setup')
        return fixture, payload

    def crash_child(arguments, **options):
        supplied = json.loads(options['input'])
        assert supplied['operation_id'] == operation_id and supplied['payload'] == payload
        namespace = supplied['admission_namespace']
        assert namespaces[0] == (f'tempo:test:{namespace}:admission:foreground',
                                 f'tempo:test:{namespace}:admission:background')
        assert arguments[-1] == '--crash-after-prepare' and options['timeout'] == 20
        return SimpleNamespace(returncode=73, stdout='Prepared 256 events', stderr='')

    def recover(supplied_operation_id):
        assert supplied_operation_id == operation_id
        admitted_stage('recovery')
        return result

    def prepare(supplied_payload):
        assert supplied_payload == payload
        admitted_stage('replay')
        return supplied_payload

    def receipt_sql(statement, parameters=()):
        row = ('opening_evidence.checkpoint', command_gateway.request_digest('opening_evidence.checkpoint', payload),
               'complete', json.dumps(result), None, None)
        return SimpleNamespace(fetchone=lambda: row if statement.startswith('SELECT command_name,request_hash,state,') else None)

    @contextmanager
    def writer(background):
        assert background is True and stages[-1] == 'replay'
        with gate.background_lease():
            yield SimpleNamespace(raw=SimpleNamespace(execute=receipt_sql))

    database = Mock()
    database.execute_native.return_value.fetchone.return_value = None
    monkeypatch.setattr(rehearsal, '_large_checkpoint_fixture', create_fixture)
    monkeypatch.setattr(rehearsal.postgres_store, 'connection', lambda **options: nullcontext(database))
    monkeypatch.setattr(rehearsal, '_fixture_scheduling', lambda *arguments: {'unchanged': True})
    monkeypatch.setattr(rehearsal, 'shadow_digest', lambda *arguments: 'unchanged-digest')
    monkeypatch.setattr(rehearsal, '_assert_large_checkpoint', lambda *arguments: None)
    monkeypatch.setattr(rehearsal, '_retain_color_provenance', lambda supplied: stages.append('retained'))
    monkeypatch.setattr(rehearsal.subprocess, 'run', crash_child)
    monkeypatch.setattr(rehearsal, '_recover_checkpoint_operation', recover)
    monkeypatch.setattr(rehearsal, '_checkpoint_operation_receipt', lambda supplied: {
        'state': 'executing', 'payload_json': json.dumps(payload), 'attempt_count': 2,
        'request_hash': command_gateway.request_digest('opening_evidence.checkpoint', payload)})
    monkeypatch.setitem(command_gateway._preparers, 'opening_evidence.checkpoint', prepare)
    monkeypatch.setitem(command_gateway._handlers, 'opening_evidence.checkpoint', Mock(side_effect=AssertionError('Completed replay must not publish twice')))
    monkeypatch.setattr(command_gateway, '_writer_connection', writer)
    return stages, namespaces, payload, operation_id


def test_checkpoint_restart_proof_isolates_persistent_parent_activity(monkeypatch, restart_admission):
    gate, server, parent_keys, parent_lease = restart_admission
    stages, namespaces, payload, operation_id = prepare_restart_proof(monkeypatch, restart_admission)
    from copy import deepcopy
    original_payload = deepcopy(payload)
    with pytest.raises(BackgroundAdmissionDeferred):
        command_gateway.execute_command(operation_id, 'opening_evidence.checkpoint', payload, background=True)
    assert stages == []
    rehearsal.test_postgres_opening_checkpoint_restart_recomputes_original_receipt()
    assert stages == ['setup', 'recovery', 'replay', 'retained']
    assert payload == original_payload
    assert len(set(namespaces)) == 1 and namespaces[0] != parent_keys
    assert server.leases == {parent_lease: float('inf')}
    assert server.deleted_keys == list(namespaces[0])
    assert (gate._FOREGROUND_KEY, gate._BACKGROUND_KEY) == parent_keys


@pytest.mark.parametrize('fail_stage', ['setup', 'recovery', 'replay'])
def test_checkpoint_restart_proof_preserves_parent_lease_and_cleans_owned_keys_on_failure(
    monkeypatch, restart_admission, fail_stage,
):
    gate, server, parent_keys, parent_lease = restart_admission
    _stages, namespaces, _payload, _operation_id = prepare_restart_proof(
        monkeypatch, restart_admission, fail_stage=fail_stage)
    from psycopg import OperationalError
    with pytest.raises(OperationalError, match='Controlled ' + fail_stage + ' failure'):
        rehearsal.test_postgres_opening_checkpoint_restart_recomputes_original_receipt()
    assert server.leases == {parent_lease: float('inf')}
    assert server.deleted_keys == list(namespaces[0])
    assert (gate._FOREGROUND_KEY, gate._BACKGROUND_KEY) == parent_keys


def test_checkpoint_restart_crash_worker_uses_parent_owned_namespace(monkeypatch, restart_admission):
    import io
    import json
    from types import SimpleNamespace
    gate, server, parent_keys, parent_lease = restart_admission
    payload = {'checkpoint': {'attempt_id': 'original-attempt'}, 'prepared_manifest': {'obsolete': True}}
    supplied = {'operation_id': 'original-operation', 'payload': payload, 'admission_namespace': 'checkpoint-restart-owned'}
    observed_keys = []

    def prepare(saved_payload):
        with gate.background_lease():
            observed_keys.append((gate._FOREGROUND_KEY, gate._BACKGROUND_KEY))
            assert saved_payload == payload and server.zscore(*parent_lease) == float('inf')
        return SimpleNamespace(observations=[None] * 20, result={'contiguous_sequence': 256})

    def deliver(operation_id, command_name, saved_payload):
        assert operation_id == supplied['operation_id'] and command_name == 'opening_evidence.checkpoint'
        command_gateway._preparers[command_name](saved_payload)

    def exit_worker(code):
        raise SystemExit(code)

    monkeypatch.setitem(command_gateway._preparers, 'opening_evidence.checkpoint', prepare)
    monkeypatch.setattr(rehearsal, 'admitted_checkpoint_delivery', deliver)
    monkeypatch.setattr(rehearsal.sys, 'stdin', io.StringIO(json.dumps(supplied)))
    monkeypatch.setattr(rehearsal.os, '_exit', exit_worker)
    with pytest.raises(SystemExit) as exited:
        rehearsal._crash_worker_after_checkpoint_preparation()
    assert exited.value.code == 73
    assert observed_keys == [('tempo:test:checkpoint-restart-owned:admission:foreground',
                              'tempo:test:checkpoint-restart-owned:admission:background')]
    assert server.leases == {parent_lease: float('inf')}
    assert (gate._FOREGROUND_KEY, gate._BACKGROUND_KEY) == parent_keys
    assert command_gateway._preparers['opening_evidence.checkpoint'] is prepare
