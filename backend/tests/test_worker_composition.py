"""Process assembly/failure cleanup, with no actual worker, signal or transport."""
import asyncio
import importlib
from types import SimpleNamespace

import pytest

from test_public_surface_infrastructure import cold_import


def test_worker_public_entry_is_cold_and_does_not_load_analysis_or_the_api():
    cold_import('app.product_composition.interfaces.worker')


def test_legacy_registration_retains_handlers_and_is_idempotent(monkeypatch):
    owner = importlib.import_module('app.product_composition.legacy_worker')
    tasks, maintenance = {}, []
    monkeypatch.setattr(owner, '_registered', False)
    monkeypatch.setattr(owner, 'register_durable_task_handler', lambda kind, handler: tasks.setdefault(kind, handler))
    monkeypatch.setattr(owner, 'register_maintenance_handler', maintenance.append)
    owner.register_legacy_handlers(); owner.register_legacy_handlers()
    assert set(tasks) == {'daily_queue', 'integrity_repair', 'opening_graph_rebuild',
        'prefix_transition_application', 'opening_segmentation', 'canonical_prefix_preview',
        'repertoire_game_refresh', 'defensive_threat_scan', 'defensive_threat_validate',
        'defensive_threat_report_audit', 'defensive_threat_backfill', 'defensive_admission',
        'defensive_rubric_audit', 'discovery_admission', 'discovery_recommendation'}
    assert tasks['repertoire_game_refresh'] is owner.execute_repertoire_game_refresh_slice
    assert maintenance == [owner._ensure_current_daily_queue, owner._ensure_daily_defense_admission,
                           owner._ensure_discovery_admissions]


def test_daily_queue_request_keeps_write_then_wake_order_and_receipt():
    from app.product_composition.legacy_worker import request_daily_queue_refresh
    events = []; receipt = {'id': 'synthetic'}; database = object()
    def enqueue(*args, **kwargs):
        events.append(('enqueue', args, kwargs)); return receipt
    def submit(operation, **kwargs):
        events.append(('write', kwargs)); operation(database)
    result = request_daily_queue_refresh(queue_date='2026-10-27', foreground=False, enqueue=enqueue,
        submit=submit, mark_refreshing=lambda db, day: events.append(('mark', db, day)),
        wake=lambda: events.append(('wake',)))
    assert result is receipt
    assert [item[0] for item in events] == ['enqueue', 'write', 'mark', 'wake']
    assert events[0][1:] == (('daily_queue', 'current', {'queue_date':'2026-10-27'}),
                            {'priority':10,'foreground':False})
    assert events[2] == ('mark', database, '2026-10-27')


@pytest.mark.parametrize('failure', ['none', 'readiness', 'writer', 'coordinator', 'stop'])
def test_legacy_worker_preserves_readiness_order_and_cleans_partial_start(monkeypatch, failure):
    root = importlib.import_module('app.product_composition.worker')
    assembly = importlib.import_module('app.product_composition.legacy_worker')
    storage = importlib.import_module('tempo_legacy_database.adapters.database_executor')
    events = []
    def step(name):
        events.append(name)
        if name == failure: raise RuntimeError('synthetic '+name)
    async def ready(): step('readiness')
    async def start(): step('coordinator')
    async def stop(): step('stop')
    monkeypatch.setattr(root, 'wait_for_api', ready)
    monkeypatch.setattr(assembly, 'register_legacy_handlers', lambda: events.append('register'))
    monkeypatch.setattr(assembly, 'coordinator', SimpleNamespace(start=start, stop=stop))
    monkeypatch.setattr(storage, 'database_writer', SimpleNamespace(start=lambda:step('writer'), stop=lambda:step('close')))
    async def run():
        stopping=asyncio.Event();stopping.set()
        await root.run_legacy_worker(stopping=stopping)
    if failure == 'none': asyncio.run(run())
    else:
        with pytest.raises(RuntimeError, match='synthetic '+failure): asyncio.run(run())
    assert events[:2] == ['register', 'readiness']
    if failure == 'readiness': assert events == ['register', 'readiness']
    else: assert events[-2:] == ['stop','close']
