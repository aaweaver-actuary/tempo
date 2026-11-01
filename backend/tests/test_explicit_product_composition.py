"""C1: compatibility lifecycle and authored Studies composition boundaries."""
import os
import subprocess
import sys
from contextlib import nullcontext

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_compatibility_worker_import_does_not_load_http_root():
    code = '''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.main':
            raise AssertionError('HTTP root imported')
sys.meta_path.insert(0, Guard())
from app.background_worker import coordinator
from app.product_composition.compatibility_coordinator import compose_compatibility_coordinator
from app.services import game_sync_coordinator as owner
assert coordinator is owner.coordinator is compose_compatibility_coordinator()
before = (dict(owner._durable_task_handlers), list(owner._maintenance_handlers))
assert compose_compatibility_coordinator() is coordinator
assert before == (owner._durable_task_handlers, owner._maintenance_handlers)
assert set(before[0]) == {'daily_queue', 'integrity_repair', 'opening_graph_rebuild',
 'prefix_transition_application', 'opening_segmentation', 'canonical_prefix_preview',
 'repertoire_game_refresh', 'defensive_threat_scan', 'defensive_threat_validate',
 'defensive_threat_report_audit', 'defensive_threat_backfill', 'defensive_admission',
 'defensive_rubric_audit', 'discovery_admission', 'discovery_recommendation'}
assert len(before[1]) == 3
'''
    subprocess.run([sys.executable, '-c', code], env=os.environ.copy(), check=True)


def test_authored_studies_router_keeps_each_refresh_callback(monkeypatch):
    from app import study_routes
    calls = []
    monkeypatch.setattr(study_routes.route_storage, 'native_commands_enabled', lambda: False)
    monkeypatch.setattr(study_routes, 'connection', lambda: nullcontext(object()))
    monkeypatch.setattr(study_routes, '_require', lambda *args: {'id': 'study'})
    monkeypatch.setattr(study_routes.route_storage, 'insert_study', lambda *args: None)
    monkeypatch.setattr(study_routes.route_storage, 'archive_study', lambda *args: None)
    monkeypatch.setattr(study_routes.route_storage, 'archive_study_cards', lambda *args: None)
    apps = [FastAPI(), FastAPI()]
    for index, app in enumerate(apps):
        app.include_router(study_routes.create_study_router(lambda index=index: calls.append(index)))
    routes = [[(route.path, tuple(sorted(route.methods or []))) for route in app.routes
               if route.path.startswith('/api/studies')] for app in apps]
    assert routes[0] == routes[1]
    with TestClient(apps[0]) as first, TestClient(apps[1]) as second:
        response = first.post('/api/studies', json={'title': 'Test', 'description': '', 'source': {}})
        assert response.status_code == 200 and 'id' in response.json()
        assert first.post('/api/studies/study/archive').json() == {'id': 'study', 'archived': True}
        assert second.post('/api/studies/study/archive').json() == {'id': 'study', 'archived': True}
        assert first.post('/api/studies/study/archive').status_code == 200
    assert calls == [0, 1, 0]


def test_registered_daily_queue_handler_preserves_task_payload(monkeypatch):
    from app.product_composition import compatibility_coordinator as composition
    from app.services import game_sync_coordinator as owner
    calls = []
    monkeypatch.setattr(composition.queue_application, 'materialize_daily_queue',
                        lambda database, day, **options: calls.append((database, day, options)))
    monkeypatch.setattr(composition, 'submit_background_write',
                        lambda operation, *, label: (calls.append(label), operation('writer')))
    composition.compose_compatibility_coordinator()
    owner._durable_task_handlers['daily_queue']({'payload': {
        'queue_date': '2026-11-01', 'preserve_through_entry_id': 42,
    }})
    assert calls == ['daily-queue:2026-11-01',
                     ('writer', '2026-11-01', {'preserve_through_entry_id': 42})]
