"""PostgreSQL discovery admission must respect its lease and published graph."""

from __future__ import annotations

from contextlib import contextmanager

import chess

from app import tasks
from app.services import discovery_admission


class Cursor:
    def __init__(self, row=None, rowcount=0):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


def test_postgres_discovery_branch_rebuild_is_admitted_once_under_lease(monkeypatch):
    assert "discovery_admission" in tasks._SUPPORTED_BACKGROUND_KINDS
    intent = {"id": "intent-one", "state": "preparing", "line_id": "line-one",
              "repertoire_id": "repertoire-one", "starting_fen": chess.STARTING_FEN,
              "preview_moves_json": '["e2e4"]'}
    enqueued = []
    inserted = []
    lease_current = True

    class Database:
        def execute(self, statement, parameters=()):
            if "SELECT generation FROM repertoire_game_scope" in statement:
                return Cursor((0,))
            if "FROM discovery_admission_intents" in statement:
                return Cursor(intent)
            if "FROM repertoire_lines" in statement:
                return Cursor()
            if "INSERT OR IGNORE INTO repertoire_lines" in statement:
                inserted.append(parameters)
                return Cursor(rowcount=1)
            if "SELECT initial_depth" in statement:
                return Cursor((2,))
            return Cursor()

    @contextmanager
    def database_connection(*, background=True):
        assert background
        yield Database()

    monkeypatch.setattr(discovery_admission.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(discovery_admission.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(discovery_admission, "background_read_connection", database_connection)
    monkeypatch.setattr(discovery_admission, "connection", database_connection)
    monkeypatch.setattr(discovery_admission, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(discovery_admission, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        enqueued.append((kind, key, payload, priority)))

    task = {"id": "admission-task", "generation": 1, "lease_token": "live",
            "payload": {"intent_id": "intent-one"}}
    discovery_admission._materialize_admission_branch(task)
    assert len(inserted) == 1
    assert enqueued == [("opening_graph_rebuild", "repertoire-one",
                         {"repertoire_id": "repertoire-one",
                          "local_day": enqueued[0][2]["local_day"]}, 40)]

    lease_current = False
    discovery_admission._materialize_admission_branch(task)
    assert len(inserted) == 1
    assert len(enqueued) == 1


def test_postgres_discovery_admission_completes_published_card_without_replay(monkeypatch):
    intent = {"id": "intent-one", "state": "preparing", "line_id": "line-one",
              "repertoire_id": "repertoire-one", "opportunity_id": "opportunity-one",
              "evidence_fingerprint": "revision-one", "created_at": "2026-01-01T00:00:00+00:00"}
    writes = []
    queued_cards = []
    completed = []
    lease_current = True

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM discovery_admission_intents" in statement:
                return Cursor(intent)
            if "FROM opening_graph_steps" in statement:
                return Cursor({"card_id": "card-one"})
            if "FROM repertoire_integrity_state" in statement:
                return Cursor({"checked_at": "2026-01-02T00:00:00+00:00",
                               "scan_status": "idle", "status": "ready"})
            if "FROM cards WHERE id=" in statement:
                return Cursor({"id": "card-one", "archived": 0,
                               "pending_validation": 0})
            if statement.lstrip().startswith("UPDATE"):
                writes.append((statement, parameters))
            return Cursor()

    @contextmanager
    def database_connection(*, background=True):
        assert background
        yield Database()

    monkeypatch.setattr(discovery_admission.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(discovery_admission.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(discovery_admission, "background_read_connection", database_connection)
    monkeypatch.setattr(discovery_admission, "connection", database_connection)
    monkeypatch.setattr(discovery_admission, "_materialize_admission_branch", lambda _task: None)
    monkeypatch.setattr(discovery_admission, "_recovery_identity", lambda *_a, **_k: ("accepted", intent))
    from app.services import postgres_integrity
    monkeypatch.setattr(postgres_integrity, "try_hold_current_integrity_certificate", lambda *_: {"current": True})
    monkeypatch.setattr(discovery_admission.storage, "try_lock_admission_card", lambda *_: {"id": "card-one", "archived": 0, "pending_validation": 0})
    monkeypatch.setattr(discovery_admission, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(discovery_admission, "ensure_card_queued_after",
                        lambda _database, card_id, **_kwargs: queued_cards.append(card_id))
    monkeypatch.setattr(discovery_admission, "complete_task_slice_in_transaction",
                        lambda _database, _task: completed.append(True) or True)

    task = {"id": "admission-task", "generation": 1, "lease_token": "live",
            "payload": {"intent_id": "intent-one"}}
    assert not discovery_admission.execute_admission_intent_slice(task)
    assert queued_cards == ["card-one"]
    assert completed == [True]
    assert any("UPDATE discovery_admission_intents" in statement for statement, _ in writes)
    opportunity_write = next((statement, parameters) for statement, parameters in writes
                             if "UPDATE repertoire_opportunities" in statement)
    assert "WHERE id=? AND evidence_fingerprint=?" in opportunity_write[0]
    assert opportunity_write[1][-1] == "revision-one"

    lease_current = False
    assert not discovery_admission.execute_admission_intent_slice(task)
    assert queued_cards == ["card-one"]
    assert completed == [True]


def test_recovery_identity_rejects_forged_and_missing_intents(monkeypatch):
    import json
    row = {'id': 'task', 'kind': 'discovery_admission', 'deduplication_key': 'intent',
           'payload_json': json.dumps({'intent_id': 'intent'}), 'generation': 2,
           'lease_token': 'lease', 'state': 'leased'}
    task = {**row, 'payload': {'intent_id': 'intent'}}
    monkeypatch.setattr(discovery_admission.storage, 'recovery_task', lambda *a, **k: row)
    monkeypatch.setattr(discovery_admission.storage, 'intent', lambda *a, **k: {'id': 'intent', 'state': 'preparing'})
    assert discovery_admission._recovery_identity(None, task)[0] == 'accepted'
    assert discovery_admission._recovery_identity(None, {**task, 'payload': {'intent_id': 'other'}})[0] == 'stale'
    row['deduplication_key'] = 'other'
    assert discovery_admission._recovery_identity(None, {**task, 'deduplication_key': 'other'})[0] == 'invalid'
    row['deduplication_key'] = 'intent'
    monkeypatch.setattr(discovery_admission.storage, 'intent', lambda *a, **k: None)
    assert discovery_admission._recovery_identity(None, task)[0] == 'invalid'


def test_recovery_stale_envelope_does_not_complete_but_missing_intent_does(monkeypatch):
    from contextlib import nullcontext
    import json
    row = {'id': 'task', 'kind': 'discovery_admission', 'deduplication_key': 'intent',
           'payload_json': json.dumps({'intent_id': 'intent'}), 'generation': 2,
           'lease_token': 'lease', 'state': 'leased'}
    task = {**row, 'payload': {'intent_id': 'intent'}}
    completions = []
    monkeypatch.setattr(discovery_admission.storage, 'configured', lambda: True)
    monkeypatch.setattr(discovery_admission, 'connection', lambda **_: nullcontext(object()))
    monkeypatch.setattr(discovery_admission.storage, 'recovery_task', lambda *a, **k: row)
    monkeypatch.setattr(discovery_admission.storage, 'intent', lambda *a, **k: None)
    monkeypatch.setattr(discovery_admission.activity_gate, 'wait_for_foreground', lambda: None)
    monkeypatch.setattr(discovery_admission, '_materialize_admission_branch', lambda *_: (_ for _ in ()).throw(AssertionError('materialized')))
    monkeypatch.setattr(discovery_admission, 'complete_task_slice_in_transaction', lambda _db, item: completions.append(item['id']))
    assert discovery_admission.execute_admission_intent_slice({**task, 'payload': {'intent_id': 'other'}}) is True
    assert completions == []
    assert discovery_admission.execute_admission_intent_slice(task) is False
    assert completions == ['task']


def test_integrity_owner_holds_only_current_graph_certificate(monkeypatch):
    from app.services import postgres_integrity
    from tempo_legacy_database.adapters.opening_graph import worker
    calls = []
    monkeypatch.setattr(worker, 'try_lock_graph_read', lambda *_: calls.append('graph') or True)
    monkeypatch.setattr(postgres_integrity.storage, 'try_hold_certificate_authority', lambda *_: calls.append('rows') or (7, 'ready'))
    monkeypatch.setattr(postgres_integrity, 'current_integrity_certificate', lambda _db, _rid, gen: calls.append(('certificate', gen)) or {'generation': gen})
    assert postgres_integrity.try_hold_current_integrity_certificate(object(), 'r') == {'generation': 7}
    assert calls == ['graph', 'rows', ('certificate', 7)]
    monkeypatch.setattr(worker, 'try_lock_graph_read', lambda *_: False)
    assert postgres_integrity.try_hold_current_integrity_certificate(object(), 'r') is None
    monkeypatch.setattr(worker, 'try_lock_graph_read', lambda *_: True)
    monkeypatch.setattr(postgres_integrity.storage, 'try_hold_certificate_authority', lambda *_: (7, 'building'))
    assert postgres_integrity.try_hold_current_integrity_certificate(object(), 'r') is None
    monkeypatch.setattr(postgres_integrity.storage, 'try_hold_certificate_authority', lambda *_: (7, 'ready'))
    monkeypatch.setattr(postgres_integrity, 'current_integrity_certificate', lambda *_: None)
    assert postgres_integrity.try_hold_current_integrity_certificate(object(), 'r') is None
