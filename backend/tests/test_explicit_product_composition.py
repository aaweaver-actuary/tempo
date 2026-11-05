"""C1: compatibility lifecycle and authored Studies composition boundaries."""

import os
import subprocess
import sys
from contextlib import nullcontext

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_compatibility_worker_import_does_not_load_http_root():
    code = """
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
"""
    subprocess.run([sys.executable, "-c", code], env=os.environ.copy(), check=True)


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


def test_registered_daily_queue_handler_preserves_task_payload(monkeypatch):
    from app.product_composition import compatibility_coordinator as composition
    from app.services import game_sync_coordinator as owner

    calls = []
    monkeypatch.setattr(
        composition.queue_application,
        "materialize_daily_queue",
        lambda database, day, **options: calls.append((database, day, options)),
    )
    monkeypatch.setattr(
        composition,
        "submit_background_write",
        lambda operation, *, label: (calls.append(label), operation("writer")),
    )
    composition.compose_compatibility_coordinator()
    owner._durable_task_handlers["daily_queue"](
        {
            "payload": {
                "queue_date": "2026-11-01",
                "preserve_through_entry_id": 42,
            }
        }
    )
    assert calls == [
        "daily-queue:2026-11-01",
        ("writer", "2026-11-01", {"preserve_through_entry_id": 42}),
    ]


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
main.postgres_store.configured = lambda: True
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


def test_sqlite_lifespan_composes_before_writer_and_preserves_lifecycle(monkeypatch):
    import asyncio
    from app import main

    calls = []

    class Coordinator:
        async def start(self):
            calls.append("coordinator-start")

        async def stop(self):
            calls.append("coordinator-stop")

    coordinator = Coordinator()
    monkeypatch.setattr(main.postgres_store, "configured", lambda: False)
    monkeypatch.setattr(
        main,
        "compose_compatibility_coordinator",
        lambda: calls.append("compose") or coordinator,
    )
    monkeypatch.setattr(main, "initialize", lambda: calls.append("initialize"))
    monkeypatch.setattr(
        main.database_writer, "start", lambda: calls.append("writer-start")
    )
    monkeypatch.setattr(
        main.database_writer, "stop", lambda: calls.append("writer-stop")
    )
    monkeypatch.setattr(
        main,
        "submit_foreground_write",
        lambda *_args, **_kwargs: calls.append("materialize"),
    )
    from app.services import study_priority

    @__import__("contextlib").contextmanager
    def policy():
        calls.append("policy-enter")
        try:
            yield
        finally:
            calls.append("policy-exit")

    monkeypatch.setattr(study_priority, "policy_publisher", policy)
    monkeypatch.setenv("TEMPO_COORDINATOR_MODE", "embedded")

    async def run():
        async with main.lifespan(main.app):
            calls.append("serving")

    asyncio.run(run())
    assert calls == [
        "compose",
        "initialize",
        "writer-start",
        "materialize",
        "compose",
        "coordinator-start",
        "policy-enter",
        "serving",
        "policy-exit",
        "compose",
        "coordinator-stop",
        "writer-stop",
    ]


def test_canonical_prefix_save_preserves_source_and_game_refresh_without_coverage(
    monkeypatch,
):
    from app.services import canonical_prefix as owner, coverage_seed_request

    events = []
    storage = owner.command_storage
    monkeypatch.setattr(storage, "repertoire_presence", lambda *_: True)
    monkeypatch.setattr(
        owner,
        "read_prefix",
        lambda *_args, **_kwargs: {"moves": [], "revision": 2, "source_revision": 9},
    )
    monkeypatch.setattr(
        storage,
        "prefix_preview",
        lambda *_: {
            "state": "ready",
            "expected_revision": 2,
            "source_revision": 9,
            "moves_json": '["e2e4"]',
        },
    )
    monkeypatch.setattr(storage, "postgres_is_configured", lambda: True)
    monkeypatch.setattr(
        storage, "save_prefix_values", lambda *args: events.append(("save", args))
    )
    monkeypatch.setattr(
        owner,
        "enqueue_task_in_transaction",
        lambda *args, **kwargs: events.append(("task", args, kwargs)),
    )
    for name in (
        "advance_priority_source_epoch",
        "delete_priority_publication",
        "delete_introduction_priorities",
    ):
        monkeypatch.setattr(
            storage, name, lambda *args, name=name: events.append((name, args))
        )
    monkeypatch.setattr(
        coverage_seed_request,
        "request_coverage_seed_in_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("retired coverage enqueued")
        ),
    )
    database = object()
    result = owner.save_prefix(
        database,
        {
            "repertoire_id": "rep",
            "request": {"preview_id": "preview", "expected_revision": 2},
        },
    )
    assert result["revision"] == 3 and result["moves_uci"] == ["e2e4"]
    assert [event[0] for event in events] == [
        "save",
        "task",
        "advance_priority_source_epoch",
        "delete_priority_publication",
        "delete_introduction_priorities",
    ]
    assert events[1][1][1:] == ("repertoire_game_refresh", "all", {"after_game_id": ""})


def test_sqlite_prefix_save_route_wakes_without_coverage(monkeypatch):
    from app import canonical_prefix_api as api
    from app.services import repertoire_coverage

    events = []
    monkeypatch.setattr(api.storage, "postgres_is_configured", lambda: False)
    monkeypatch.setattr(api, "connection", lambda: nullcontext("writer"))
    monkeypatch.setattr(
        api,
        "save_prefix",
        lambda database, payload: events.append((database, payload)) or {"revision": 3},
    )
    monkeypatch.setattr(
        repertoire_coverage,
        "enqueue_coverage_refresh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("retired coverage enqueued")
        ),
    )
    app = FastAPI()
    app.include_router(
        api.create_canonical_prefix_router(lambda: events.append("wake"))
    )
    with TestClient(app) as client:
        response = client.put(
            "/api/repertoires/rep/canonical-prefix",
            json={"preview_id": "preview", "expected_revision": 2},
        )
    assert response.status_code == 200 and response.json() == {"revision": 3}
    assert events == [
        (
            "writer",
            {
                "repertoire_id": "rep",
                "request": {"preview_id": "preview", "expected_revision": 2},
            },
        ),
        "wake",
    ]


def test_branch_add_and_remove_preserve_repairs_without_coverage(monkeypatch):
    import chess
    from app import branch_commands as owner
    from app.services import coverage_seed_request

    events = []
    db = object()
    monkeypatch.setattr(
        coverage_seed_request,
        "request_coverage_seed_in_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("retired coverage enqueued")
        ),
    )
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
    result = owner.add_repertoire_branch.__wrapped__(db, payload)
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
    result = owner.remove_repertoire_branch.__wrapped__(db, payload)
    assert result["deleted_line_count"] == 1
    assert events == ["lock-remove", "delete", "integrity", "graph"]
