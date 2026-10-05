"""Portable command regressions; real locks/recreation are proved by Docker durability."""
from contextlib import nullcontext
import json
import sqlite3

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app import command_gateway, command_dispatch, main, pgn_import_commands


class PortableReceiptDatabase:
    def __init__(self):
        self.raw = self
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript('''
            CREATE TABLE operation_receipts(operation_id TEXT PRIMARY KEY, command_name TEXT,
            request_hash TEXT, state TEXT, payload_json TEXT, response_json TEXT, error_json TEXT,
            background BOOLEAN DEFAULT FALSE, attempt_count INTEGER DEFAULT 0,
            cycle_attempt_count INTEGER DEFAULT 0, retry_cycle INTEGER DEFAULT 0,
            next_retry_at TEXT, lease_expires_at TEXT, attempt_token TEXT, last_error_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE repertoires(id TEXT PRIMARY KEY);
        ''')

    def execute(self, statement, parameters=()):
        if 'pg_advisory_xact_lock' in statement:
            return self.connection.execute('SELECT 1')
        return self.connection.execute(statement.replace('%s', '?').replace(' FOR UPDATE', '')
                                       .replace('NOW()', 'CURRENT_TIMESTAMP'), parameters)


@pytest.fixture
def receipt_database(monkeypatch):
    database = PortableReceiptDatabase()
    monkeypatch.setattr(command_gateway, '_writer_connection', lambda _background: nullcontext(database))
    monkeypatch.setitem(command_gateway._handlers, 'imports.pgn.admit',
                        lambda stored, payload: stored.raw.execute('INSERT INTO repertoires VALUES(?)', (payload['source_name'],)))
    yield database
    database.connection.close()


@pytest.mark.parametrize('previous_state', ['unknown', 'queued', 'executing', 'retrying', 'pending', 'blocked', 'failed'])
def test_discarded_pgn_delivery_cannot_restore_its_payload_or_create_repertoire_data(receipt_database, previous_state):
    payload = {'source_name': 'discarded.pgn'}
    if previous_state != 'unknown':
        receipt_database.execute('INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,attempt_token,next_retry_at,lease_expires_at) VALUES(?,?,?,?,?,?,?,?)',
                                 ('old-import', 'imports.pgn.admit', command_gateway.request_digest('imports.pgn.admit', payload), previous_state, json.dumps(payload), 'old-token', 'future', 'future'))
    for _repeat in range(2):
        assert pgn_import_commands.discard_pgn_import(receipt_database, {'operation_id': 'old-import'}) == {'operation_id': 'old-import', 'outcome': 'discarded'}
    if previous_state == 'unknown':
        with pytest.raises(command_gateway.CommandConflict):
            command_gateway.record_operation_attempt('old-import', 'imports.pgn.admit', payload, background=False)
    else:
        assert command_gateway.record_operation_attempt('old-import', 'imports.pgn.admit', payload, background=False)[0] is False
    with pytest.raises((command_gateway.CommandConflict, RuntimeError)):
        command_gateway.execute_command('old-import', 'imports.pgn.admit', payload, attempt_token='old-token')
    assert receipt_database.execute('SELECT state,payload_json,attempt_token,next_retry_at,lease_expires_at,last_error_json FROM operation_receipts').fetchone() == ('failed', None, None, None, None, None)
    assert receipt_database.execute('SELECT COUNT(*) FROM repertoires').fetchone()[0] == 0


def test_pgn_discard_preserves_completed_import_and_rejects_other_command_types(receipt_database):
    completed = {'repertoire_id': 'kept'}
    for operation_id, command_name, state in [('finished', 'imports.pgn.admit', 'complete'), ('review', 'reviews.save', 'queued')]:
        receipt_database.execute('INSERT INTO operation_receipts(operation_id,command_name,state,response_json,payload_json) VALUES(?,?,?,?,?)', (operation_id, command_name, state, json.dumps(completed), 'original payload'))
    original = list(receipt_database.execute('SELECT * FROM operation_receipts'))
    assert pgn_import_commands.discard_pgn_import(receipt_database, {'operation_id': 'finished'}) == {'operation_id': 'finished', 'outcome': 'already_complete', 'import_result': completed}
    with pytest.raises(HTTPException, match='Only PGN'):
        pgn_import_commands.discard_pgn_import(receipt_database, {'operation_id': 'review'})
    assert list(receipt_database.execute('SELECT * FROM operation_receipts')) == original


def test_pgn_discard_route_uses_stable_foreground_command_identity(monkeypatch):
    dispatched = []
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(main, 'read_operation', lambda _identity: {'state': 'unknown'})
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda name, payload, *, idempotency_key: dispatched.append((name, payload, idempotency_key)) or {'operation_id': payload['operation_id'], 'outcome': 'discarded'})
    for _repeat in range(2):
        response = TestClient(main.app).post('/api/imports/pgn/old-import/discard')
        assert response.status_code == 200, response.text
        assert response.json() == {'operation_id': 'old-import', 'outcome': 'discarded'}
    assert dispatched[0] == dispatched[1]
    assert dispatched[0][0:2] == ('imports.pgn.discard', {'operation_id': 'old-import'})
    assert dispatched[0][2] != 'old-import'

@pytest.mark.parametrize('postgres_configured, operation_id, status_code', [(False, 'old-import', 404), (True, 'x' * 129, 422)])
def test_pgn_discard_invalid_route_requests_do_not_dispatch(monkeypatch, postgres_configured, operation_id, status_code):
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: postgres_configured)
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda *_args, **_kwargs: pytest.fail('Invalid discard must not dispatch'))
    client = TestClient(main.app)
    try:
        response = client.post(f'/api/imports/pgn/{operation_id}/discard')
    finally:
        client.close()
    assert response.status_code == status_code
    assert response.json()['detail']
