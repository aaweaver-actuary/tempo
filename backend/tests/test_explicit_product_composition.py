"""C1: compatibility lifecycle and authored Studies composition boundaries."""

import os
import subprocess
import sys
from contextlib import nullcontext

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_authored_studies_router_keeps_each_refresh_callback(monkeypatch):
    from app import study_routes

    calls = []
    monkeypatch.setattr(
        study_routes.route_storage, "native_commands_enabled", lambda: False
    )
    monkeypatch.setattr(study_routes, "connection", lambda: nullcontext(object()))
    monkeypatch.setattr(study_routes, "_require", lambda *args: {"id": "study"})
    monkeypatch.setattr(study_routes.route_storage, "insert_study", lambda *args: None)
    monkeypatch.setattr(study_routes.route_storage, "archive_study", lambda *args: None)
    monkeypatch.setattr(
        study_routes.route_storage, "archive_study_cards", lambda *args: None
    )
    apps = [FastAPI(), FastAPI()]
    for index, app in enumerate(apps):
        app.include_router(
            study_routes.create_study_router(lambda index=index: calls.append(index))
        )
    routes = [
        [
            (route.path, tuple(sorted(route.methods or [])))
            for route in app.routes
            if route.path.startswith("/api/studies")
        ]
        for app in apps
    ]
    assert routes[0] == routes[1]
    with TestClient(apps[0]) as first, TestClient(apps[1]) as second:
        response = first.post(
            "/api/studies", json={"title": "Test", "description": "", "source": {}}
        )
        assert response.status_code == 200 and "id" in response.json()
        assert first.post("/api/studies/study/archive").json() == {
            "id": "study",
            "archived": True,
        }
        assert second.post("/api/studies/study/archive").json() == {
            "id": "study",
            "archived": True,
        }
        assert first.post("/api/studies/study/archive").status_code == 200
    assert calls == [0, 1, 0]


def test_postgres_main_cold_lifespan_excludes_legacy_sync_and_providers():
    code = """
import asyncio, importlib.abc, os, socket, subprocess, sys
from contextlib import contextmanager, nullcontext
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'app.services.game_sync_coordinator', 'app.services.game_sync',
                        'app.games.providers.lichess', 'app.games.providers.chesscom',
                        'app.product_composition.feedback_commands',
                        'app.product_composition.game_history_runtime',
                        'app.product_composition.game_sync',
                        'app.services.repertoire_coverage',
                        'app.services.threat_pipeline'}:
            raise AssertionError('legacy import: ' + fullname)
def forbidden(*args, **kwargs): raise AssertionError('network or process startup')
sys.meta_path.insert(0, Guard())
socket.socket.connect = forbidden
socket.create_connection = forbidden
subprocess.Popen = forbidden
os.environ['TEMPO_DATABASE_READ_URL'] = 'postgresql://reader@example/tempo'
os.environ.pop('TEMPO_DATABASE_WRITE_URL', None)
from app import main, command_dispatch
from app.services import study_priority
calls = []
main.initialize = lambda: calls.append('initialize')
command_dispatch.dispatch_command = lambda name, payload, idempotency_key: calls.append(('dispatch', name, payload, idempotency_key)) or {'ok':True}
@contextmanager
def policy():
    calls.append('policy-enter')
    try: yield
    finally: calls.append('policy-exit')
study_priority.policy_publisher = policy
from app import queue_commands
from app.services import queue_next_card
queue_commands.current_study_day = lambda db: '2026-01-01'
main.read_connection = lambda: nullcontext(object())
main.queue_projection_storage.evidence_timezone = lambda *args: ('UTC',)
main.queue_projection_storage.queue_projection = lambda *args: None
main.queue_projection_storage.queue_diagnostics = lambda *args: []
main.queue_projection_storage.queue_rows = lambda *args, **kwargs: []
queue_next_card.pending_next_card_preparation = lambda *args: False
async def run():
    async with main.lifespan(main.app):
        result = main.queue_today()
        assert result['local_date'] == '2026-01-01' and result['cards'] == [] and result['count'] == 0
        calls.append('serving')
asyncio.run(run())
assert '/api/game-evidence/product/feedback' in {route.path for route in main.app.routes}
assert not {'/api/product/game-history/preview', '/api/repertoires/{identifier}/coverage'} & {route.path for route in main.app.routes}
assert calls[0] == 'initialize'
assert calls[1][0:2] == ('dispatch','queue.ensure_current') and calls[1][2]['queue_date'] and calls[1][3] is None
assert calls[2:] == ['policy-enter','serving','policy-exit']
assert not {'app.services.game_sync_coordinator','app.services.game_sync',
            'app.games.providers.lichess','app.games.providers.chesscom'} & sys.modules.keys()
try: main.unknown_coordinator_export
except AttributeError: pass
else: raise AssertionError('unknown export accepted')
"""
    subprocess.run([sys.executable, "-c", code], env=os.environ.copy(), check=True)


def test_branch_add_and_remove_preserve_repairs(monkeypatch):
    import chess
    from app import branch_commands as owner

    events = []
    db = object()
    monkeypatch.setattr(
        owner.storage,
        "lock_repertoire_for_add",
        lambda *_: events.append("lock-add") or ("rep",),
    )
    monkeypatch.setattr(
        owner.change_storage,
        "lock_sources",
        lambda *_: events.append("lock-sources") or (),
    )
    monkeypatch.setattr(
        owner, "validate_source_rows", lambda *_: events.append("validate")
    )
    monkeypatch.setattr(
        owner, "ensure_line_in_scope", lambda *_args, **_kwargs: "route"
    )
    monkeypatch.setattr(
        owner.storage,
        "insert_line_values",
        lambda *_: events.append("insert") or ("line",),
    )
    monkeypatch.setattr(owner.storage, "initial_training_depth", lambda *_: 2)
    monkeypatch.setattr(
        owner.storage, "set_line_training_depth", lambda *_: events.append("depth")
    )
    monkeypatch.setattr(
        owner, "certify_admitted_route", lambda *_: events.append("certify")
    )
    monkeypatch.setattr(
        owner,
        "invalidate_integrity_in_transaction",
        lambda *_: events.append("integrity"),
    )
    monkeypatch.setattr(
        owner, "request_graph_rebuild_in_transaction", lambda *_: events.append("graph")
    )
    monkeypatch.setattr(owner, "integrity_summary", lambda *_: {"status": "unchecked"})
    payload = {
        "repertoire_id": "rep",
        "starting_fen": chess.STARTING_FEN,
        "moves": ["e2e4"],
        "trained_color": "white",
    }
    result = owner.add_repertoire_branch(db, payload)
    assert result["duplicate"] is False
    assert events == [
        "lock-add",
        "lock-sources",
        "validate",
        "insert",
        "depth",
        "certify",
        "integrity",
        "graph",
    ]
    events.clear()
    monkeypatch.setattr(
        owner.storage,
        "lock_repertoire_for_remove",
        lambda *_: events.append("lock-remove") or ("rep",),
    )
    monkeypatch.setattr(
        owner.storage,
        "stored_repertoire_lines",
        lambda *_: [
            {"id": "line", "start_fen": chess.STARTING_FEN, "moves_json": '["e2e4"]'}
        ],
    )
    monkeypatch.setattr(
        owner.storage, "delete_matching_lines", lambda *_: events.append("delete")
    )
    result = owner.remove_repertoire_branch(db, payload)
    assert result["deleted_line_count"] == 1
    assert events == ["lock-remove", "delete", "integrity", "graph"]
