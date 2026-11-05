"""Native identity fence proof on a parent-owned disposable fixture.

This is only the stale/missing boundary, not the graph-to-card journey.
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.services.discovery_admission import _recovery_identity
from tempo_legacy_database.adapters import postgres_store


@pytest.fixture
def owned_database():
    name = os.environ.get('TEMPO_DISCOVERY_ACCEPTANCE_FIXTURE', '')
    if not re.fullmatch(r'tempo_discovery_acceptance_[0-9a-f]{32}', name):
        raise RuntimeError('Parent-owned disposable discovery fixture required')
    urls = [os.environ[key] for key in ('TEMPO_DISCOVERY_ACCEPTANCE_ADMIN',
                                         'TEMPO_DATABASE_READ_URL', 'TEMPO_DATABASE_WRITE_URL')]
    targets = [conninfo_to_dict(url) for url in urls]
    if (os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable'
            or any(item.get('dbname') != name or item.get('host') not in {'127.0.0.1', 'localhost'} for item in targets)
            or len({(item['host'], item.get('port', '5432')) for item in targets}) != 1):
        raise RuntimeError('Only the named disposable loopback fixture is permitted')
    with psycopg.connect(urls[0]) as database:
        assert database.execute('SELECT current_database()').fetchone()[0] == name
    postgres_store.close_pools()
    yield
    postgres_store.close_pools()


def test_native_missing_intent_and_forged_envelope_are_distinct(owned_database):
    token = uuid.uuid4().hex
    task_id, intent_id, lease = 'test-task-' + token, 'test-intent-' + token, 'lease-' + token
    now = datetime.now(timezone.utc).isoformat()
    payload = {'intent_id': intent_id}
    with postgres_store.connection(read_only=False) as database:
        database.execute_native(
            "INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,"
            "payload_version,payload_json,attempt_count,max_attempts,next_attempt_at,lease_token,"
            "lease_expires_at,created_at,updated_at) VALUES(%s,'discovery_admission',%s,1,80,'leased',"
            "'queued',1,%s,1,5,%s,%s,%s,%s,%s)",
            (task_id, intent_id, json.dumps(payload), now, lease, '2099-01-01T00:00:00+00:00', now, now))
    task = {'id': task_id, 'kind': 'discovery_admission', 'deduplication_key': intent_id,
            'generation': 1, 'lease_token': lease, 'payload': payload}
    with postgres_store.connection(read_only=False) as database:
        assert _recovery_identity(database, {**task, 'payload': {'intent_id': 'other'}}, lock=True)[0] == 'stale'
        assert _recovery_identity(database, task, lock=True)[0] == 'invalid'
        row = database.execute_native('SELECT state,lease_token FROM background_tasks WHERE id=%s', (task_id,)).fetchone()
        assert tuple(row) == ('leased', lease)


def test_native_owner_retry_disabled_and_missing_intent_preserve_parked_task(owned_database, monkeypatch):
    """Neither an unqualified claimant nor a missing accepted intent can wake work."""
    from fastapi import HTTPException
    from app import activity_commands

    token = uuid.uuid4().hex
    task_id, intent_id = 'parked-task-' + token, 'parked-intent-' + token
    now = datetime.now(timezone.utc).isoformat()
    parked = '9999-12-31T23:59:59+00:00'
    with postgres_store.connection(read_only=False) as database:
        database.execute_native(
            "INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,"
            "payload_version,payload_json,attempt_count,max_attempts,next_attempt_at,created_at,updated_at) "
            "VALUES(%s,'discovery_admission',%s,7,80,'queued','queued',1,%s,2,5,%s,%s,%s)",
            (task_id, intent_id, json.dumps({'intent_id': intent_id}), parked, now, now))
    payload = {'task_id': task_id, 'generation': 7, 'intent_id': intent_id,
               'hold_reason': 'graph_prerequisite_missing'}
    with postgres_store.connection(read_only=False) as database:
        with pytest.raises(HTTPException) as disabled:
            activity_commands._retry_retained_discovery(database, payload)
        assert disabled.value.status_code == 409
        assert disabled.value.detail == {'code': 'discovery_recovery_disabled'}
    # Simulate qualification only in this disposable test; the real gate remains false.
    monkeypatch.setattr(activity_commands, '_DISCOVERY_RECOVERY_QUALIFIED', True)
    for attempted in (payload, {**payload, 'generation': 8}, {**payload, 'intent_id': 'other'}):
        with postgres_store.connection(read_only=False) as database:
            with pytest.raises(HTTPException) as rejected:
                activity_commands._retry_retained_discovery(database, attempted)
            assert rejected.value.status_code == 409
    postgres_store.close_pools()  # Recovery checks must survive reconnect.
    with postgres_store.connection(read_only=True) as database:
        row = database.execute_native(
            'SELECT id,generation,state,next_attempt_at,lease_token,attempt_count FROM background_tasks WHERE id=%s',
            (task_id,)).fetchone()
        assert tuple(row) == (task_id, 7, 'queued', parked, None, 2)
        assert database.execute_native(
            "SELECT count(*) FROM background_task_events WHERE task_id=%s AND event='manual_retry'",
            (task_id,)).fetchone()[0] == 0


def test_native_missing_graph_parks_accepted_intent_without_changing_identity(owned_database, monkeypatch):
    """An accepted operation with no graph owner is held, not anonymously retried."""
    import chess
    from app.services import discovery_admission

    token = uuid.uuid4().hex
    repertoire, opportunity = 'rep-' + token, 'opp-' + token
    intent_id, task_id, lease = 'intent-' + token, 'task-' + token, 'lease-' + token
    now = datetime.now(timezone.utc).isoformat()
    payload = {'intent_id': intent_id}
    with postgres_store.connection(read_only=False) as database:
        database.execute_native(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'fixture',%s)",
            (repertoire, 'Retained recovery fixture', now))
        database.execute_native(
            "INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,status,score,evidence_json,"
            "evidence_fingerprint,created_at,updated_at) VALUES(%s,%s,'missing_response',%s,'active',1,"
            "'{}','fixture-revision',%s,%s)",
            (opportunity, repertoire, ' '.join(chess.STARTING_FEN.split()[:4]), now, now))
        database.execute_native(
            "INSERT INTO discovery_admission_intents(id,opportunity_id,repertoire_id,evidence_fingerprint,"
            "starting_fen,selected_move_uci,preview_moves_json,recommendation_json,line_id,state,created_at,updated_at) "
            "VALUES(%s,%s,%s,'fixture-revision',%s,'e2e4','[\"e2e4\"]','{}',%s,'preparing',%s,%s)",
            (intent_id, opportunity, repertoire, chess.STARTING_FEN, 'line-' + token, now, now))
        database.execute_native(
            "INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,"
            "payload_version,payload_json,attempt_count,max_attempts,next_attempt_at,lease_token,"
            "lease_expires_at,created_at,updated_at) VALUES(%s,'discovery_admission',%s,3,80,'leased',"
            "'queued',1,%s,1,5,%s,%s,%s,%s,%s)",
            (task_id, intent_id, json.dumps(payload), now, lease, '2099-01-01T00:00:00+00:00', now, now))
    task = {'id': task_id, 'kind': 'discovery_admission', 'deduplication_key': intent_id,
            'generation': 3, 'lease_token': lease, 'payload': payload}
    monkeypatch.setattr(discovery_admission.activity_gate, 'wait_for_foreground', lambda: None)
    assert discovery_admission.execute_admission_intent_slice(task) is True
    postgres_store.close_pools()
    with postgres_store.connection(read_only=True) as database:
        row = database.execute_native(
            'SELECT id,generation,state,next_attempt_at,lease_token FROM background_tasks WHERE id=%s',
            (task_id,)).fetchone()
        assert tuple(row) == (task_id, 3, 'queued', '9999-12-31T23:59:59+00:00', None)
        intent = database.execute_native(
            'SELECT id,state,last_error FROM discovery_admission_intents WHERE id=%s', (intent_id,)).fetchone()
        assert tuple(intent) == (intent_id, 'preparing', 'graph_prerequisite_missing')
        assert database.execute_native('SELECT count(*) FROM repertoire_lines WHERE id=%s',
                                       ('line-' + token,)).fetchone()[0] == 0
