"""Direct graph rehearsals yield without replacing their durable claim."""
import ast
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import redis_admission_gate
from app.services.activity_gate import ApplicationActivityGate


@pytest.mark.parametrize('operation_name', ['prepare_obsolete_graph_cards', 'execute_graph_cleanup_slice'])
def test_graph_rehearsal_yields_to_real_policy_section_without_reclaiming_lease(monkeypatch, operation_name):
    path = Path(__file__).resolve().parents[2] / 'database/tests/check_postgres_graph_retention.py'
    monkeypatch.syspath_prepend(str(path.parent))
    specification = importlib.util.spec_from_file_location('graph_admission_proof', path)
    proof = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(proof)
    fixture = proof.IncidentFixture()
    task = {'id': 'original-task', 'generation': 7, 'lease_token': 'original-lease', 'phase': 'cleanup'}
    monkeypatch.setattr(fixture, 'history', Mock(return_value=[]))
    monkeypatch.setattr(fixture, 'task', Mock(return_value=task))
    monkeypatch.setattr(fixture, 'claim', Mock(return_value=task))
    monkeypatch.setattr(fixture, 'capture', Mock(return_value=nullcontext()))
    monkeypatch.setattr(fixture, 'invoke_worker_slice', Mock(return_value=True))
    monkeypatch.setattr(fixture, 'state', Mock(return_value={
        'phase': 'cleanup', 'payload': {'after_card_id': fixture.card_id(1)}}))
    monkeypatch.setattr(fixture, 'link_state', Mock(return_value={'links': [fixture.repertoire_id]}))
    monkeypatch.setattr(proof.postgres_store, 'close_pools', Mock())
    monkeypatch.setattr(proof.graph, 'prepare_obsolete_graph_cards', Mock(return_value=SimpleNamespace(
        obsolete_card_ids=(fixture.card_id(0), fixture.card_id(1)), checkpoint_card_id=fixture.card_id(1))))
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: False)
    gate = ApplicationActivityGate()
    entered, release = Event(), Event()
    attempts, effects = [], []

    class ProbeFinished(RuntimeError):
        """Stop after the real admission boundary, before any fixture SQL."""

    def publisher_read():
        with gate.background_control(), gate.background_database_section():
            entered.set()
            assert release.wait(2), 'The direct rehearsal did not yield to its policy reader'

    def execute(*arguments):
        attempts.append(arguments)
        with gate.background_job('opening_graph_rebuild', 'original-task', yielding=True), gate.background_database_section():
            effects.append(arguments)
        raise ProbeFinished('The original operation obtained admission')

    monkeypatch.setattr(proof.graph, operation_name, execute)
    with ThreadPoolExecutor(max_workers=1) as executor:
        publisher = executor.submit(publisher_read)
        assert entered.wait(2)
        try:
            import database.tooling.postgres_rehearsal_admission as postgres_rehearsal_admission
            original_sleep = postgres_rehearsal_admission.time.sleep

            def yield_to_policy(_seconds):
                assert not effects
                assert fixture.claim.call_count == (operation_name == 'execute_graph_cleanup_slice')
                assert task['lease_token'] == 'original-lease'
                release.set()
                publisher.result(timeout=2)
                original_sleep(0)

            monkeypatch.setattr(postgres_rehearsal_admission.time, 'sleep', yield_to_policy)
            with pytest.raises(ProbeFinished, match='original operation obtained admission'):
                fixture.test_postgres_graph_cleanup_current_pages_restart_and_shared_tail()
        finally:
            release.set()
            publisher.result(timeout=2)
    assert len(attempts) == 2 and len(effects) == 1
    if operation_name == 'execute_graph_cleanup_slice':
        assert all(arguments[0] is task for arguments in attempts)
        fixture.claim.assert_called_once_with('original-task')
    else:
        assert attempts == [(fixture.repertoire_id, 7, '')] * 2
        fixture.claim.assert_not_called()
    fixture.task.assert_called_once_with('opening_graph_rebuild')
    assert task == {'id': 'original-task', 'generation': 7, 'lease_token': 'original-lease', 'phase': 'cleanup'}


@pytest.mark.parametrize('filename', ['check_postgres_graph_retention.py', 'check_postgres_canonical_freshness.py'])
def test_native_graph_direct_entry_calls_use_shared_admission_protocol(filename):
    path = Path(__file__).resolve().parents[2] / 'database' / 'tests' / filename
    tree = ast.parse(path.read_text())
    targets = {'prepare_obsolete_graph_cards', 'execute_graph_cleanup_slice'}

    def operation_name(operation):
        return operation.attr if isinstance(operation, ast.Attribute) else operation.id if isinstance(operation, ast.Name) else None

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    unguarded = [node.lineno for node in calls if operation_name(node.func) in targets]
    guarded = [node for node in calls if operation_name(node.func) == 'admitted_call'
               and node.args and operation_name(node.args[0]) in targets]
    assert guarded, 'The selected native driver must exercise real graph admission'
    assert not unguarded, f'{filename} bypasses ordinary admission at lines {unguarded}'
