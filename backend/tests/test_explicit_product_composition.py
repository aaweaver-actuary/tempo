"""Retained lifecycle, Study routes and repertoire composition boundaries."""

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest


def test_postgres_main_cold_lifespan_excludes_legacy_sync_and_providers():
    code = """
import asyncio, importlib.abc, os, socket, subprocess, sys
from contextlib import contextmanager, nullcontext
from datetime import date
from types import SimpleNamespace
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
main.capture_study_calendar = lambda db: SimpleNamespace(day=date(2026, 1, 1))
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
paths = {route.path for route in main.app.routes}
prefix = '/api/studies/{study_id}/exercises/{exercise_id}'
assert {prefix + '/present', prefix + '/attempts', prefix + '/attempts/{attempt_id}/self-assess', prefix + '/attempts/{attempt_id}/feedback'} <= paths
assert '/api/game-evidence/product/feedback' not in paths
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
    calendar = SimpleNamespace(
        day=date(2026, 1, 1), observed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    monkeypatch.setattr(
        owner,
        "capture_study_calendar",
        lambda database: (
            calendar if database is db else pytest.fail("wrong calendar database")
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

    def insert_line(database, values):
        assert database is db and values[-1] == calendar.observed_at.isoformat()
        events.append("insert")
        return ("line",)

    monkeypatch.setattr(owner.storage, "insert_line_values", insert_line)
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

    def request_graph(database, repertoire_id, day):
        assert (
            database is db
            and repertoire_id == "rep"
            and day == calendar.day.isoformat()
        )
        events.append("graph")

    monkeypatch.setattr(owner, "request_graph_rebuild_in_transaction", request_graph)
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
