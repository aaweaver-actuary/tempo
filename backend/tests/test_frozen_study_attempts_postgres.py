"""Native frozen-study route/receipt proof; run only in the owned PostgreSQL fixture."""
import json
import hashlib
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from native_study_fixture import owned_postgres

from app import command_dispatch, command_gateway, study_attempt_commands, study_exercise_routes
from app.contracts.command_identity import request_digest
from app.services.study_grading import GRADER_VERSION

FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
SPEC = {"type": "choice", "prompt": "Choose", "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct_option_ids": ["a"]}
EXPLAIN = {"type": "explanation", "prompt": "Explain", "rubric": "Original rubric"}
BASE = "/api/studies/frozen/exercises/"


@pytest.fixture
def frozen(owned_postgres, monkeypatch):
    with owned_postgres() as db:
        db.execute("INSERT INTO studies(id,title,created_at,updated_at) VALUES('frozen','Frozen','2026-01-01','2026-01-01')")
        db.execute("INSERT INTO study_chapters(id,study_id,title,position) VALUES('chapter','frozen','Chapter',1)")
        db.execute("INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,filename,record_index,headers_json,diagnostics_json,valid,created_at) VALUES('source','chapter','group',1,'','digest','fixture',0,'{}','{}',1,'2026-01-01')")
        db.execute("INSERT INTO study_positions(id,source_id,child_index,fen,history_json,node_path,valid) VALUES('position','source',0,%s,'[]','root',1)", (FEN,))
        for exercise, specification in (("choice", SPEC), ("explain", EXPLAIN)):
            db.execute("INSERT INTO study_exercises(id,study_id,position_id,current_revision,status,created_at,updated_at) VALUES(%s,'frozen','position',1,'published','2026-01-01','2026-01-01')", (exercise,))
            db.execute("INSERT INTO study_exercise_revisions(exercise_id,revision,specification_json,specification_hash,created_at) VALUES(%s,1,%s,'original-hash','2026-01-01')", (exercise, json.dumps(specification)))
            db.execute("INSERT INTO cards(id,repertoire_id,kind,content_type,study_exercise_id,start_fen,moves_json,state,due_date,revision) VALUES(%s,NULL,'exercise','study_exercise',%s,%s,'[]','learning',%s,1)", ("card-" + exercise, exercise, FEN, date.today().isoformat()))
    # Every production preparation and handler connection must use this
    # fixture's owned database. Any unbound pool access fails before a socket.
    from tempo_storage import connections, postgres
    def unbound_pool(*args, **kwargs):
        raise AssertionError("Unbound PostgreSQL capability in owned fixture")
    def owned_connection(*args, **kwargs):
        return owned_postgres()
    monkeypatch.setattr(postgres, '_pool', unbound_pool)
    for name in ('connection', 'read_connection', 'background_connection', 'background_read_connection', 'foreground_connection'):
        monkeypatch.setattr(connections, name, owned_connection)
    monkeypatch.setattr(study_exercise_routes, 'read_connection', owned_connection)
    monkeypatch.setattr(command_gateway, '_writer_connection', lambda background: owned_postgres())
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda name, payload, *, idempotency_key: command_gateway.execute_command(idempotency_key, name, payload))
    # Simulate failure at the advisory broker connection boundary without ever
    # opening a socket. The real post-commit wake handler must absorb it.
    from app.celery_app import celery_app
    wake_attempts = []
    def unavailable_wake(*args, **kwargs):
        wake_attempts.append((args, kwargs))
        raise ConnectionError("owned fixture: advisory broker unavailable")
    monkeypatch.setattr(celery_app, 'connection_for_write', unavailable_wake)
    app = FastAPI(); app.include_router(study_exercise_routes.router)
    client = TestClient(app)
    client.fixture_wake_attempts = wake_attempts
    return client, owned_postgres


def _frozen_queued_attempt_receipt_and_lost_ack_replay(frozen, option, rating):
    client, transaction = frozen
    with transaction() as db:
        day = date.today().isoformat()
        cycle = db.execute("SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=%s AND card_id='card-choice'", (day,)).fetchone()[0]
        entry = db.execute("INSERT INTO daily_queue(queue_date,card_id,position,cycle) VALUES(%s,'card-choice',0,%s) RETURNING id", (day, cycle)).fetchone()[0]
    attempt_id, operation_id = f"original-attempt-{rating}", f"original-operation-{rating}"
    request = {"attempt_id": attempt_id, "revision": 1, "context": "review", "queue_entry_id": entry,
               "queue_cycle": cycle, "card_id": "card-choice", "answer": {"type": "choice", "option_ids": [option]}}
    present = client.get(BASE + 'choice/present')
    assert present.status_code == 200 and 'correct_option_ids' not in present.text
    response = client.post(BASE + 'choice/attempts', json=request, headers={'Idempotency-Key': operation_id})
    assert response.status_code == 200 and response.json()['rating'] == rating and response.json()['persisted'] is True
    assert client.post(BASE + 'choice/attempts', json=request, headers={'Idempotency-Key': operation_id}).json() == response.json()
    with transaction() as db:
        attempt = db.execute("SELECT grader_version,answer_hash FROM study_attempts WHERE id=%s", (attempt_id,)).fetchone()
        assert attempt['grader_version'] == GRADER_VERSION == 1
        original_request = study_exercise_routes.StudyAttemptRequest.model_validate(request).model_dump(mode='json')
        original_hash = hashlib.sha256(json.dumps({'request': original_request}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert attempt['answer_hash'] == original_hash
        assert db.execute("SELECT count(*) FROM study_attempts WHERE id=%s", (attempt_id,)).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM reviews WHERE source_ref=%s", (attempt_id,)).fetchone()[0] == 1
        receipt = db.execute("SELECT request_hash,state FROM operation_receipts WHERE operation_id=%s", (operation_id,)).fetchone()
        payload = {'study_id': 'frozen', 'exercise_id': 'choice', 'attempt': study_exercise_routes.StudyAttemptRequest.model_validate(request).model_dump(mode='json')}
        assert receipt['request_hash'] == request_digest('studies.attempts.submit', payload) and receipt['state'] == 'complete'
        # The failed wake follows commit; durable queue work must still be
        # discoverable by the existing periodic recovery owner.
        assert client.fixture_wake_attempts
        task = db.execute("SELECT state,payload_json FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
        projection = db.execute("SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s", (date.today().isoformat(),)).fetchone()
        assert task and task['state'] in ('queued', 'leased', 'retrying')
        assert json.loads(task['payload_json'])['queue_date'] == date.today().isoformat()
        assert projection and projection['requested_refresh_epoch'] > 0
        db.execute("INSERT INTO study_exercise_revisions(exercise_id,revision,specification_json,specification_hash,created_at) VALUES('choice',2,%s,'new-hash','2026-01-02') ON CONFLICT(exercise_id,revision) DO NOTHING", (json.dumps({**SPEC, 'correct_option_ids': ['b']}),))
        db.execute("UPDATE study_exercises SET current_revision=2 WHERE id='choice'")
    assert client.get(BASE + f'choice/attempts/{attempt_id}/feedback').json()['specification']['correct_option_ids'] == ['a']
    assert client.get(f'/api/studies/other/exercises/choice/attempts/{attempt_id}/feedback').status_code == 404
    with transaction() as db:
        db.execute("UPDATE study_exercises SET current_revision=1 WHERE id='choice'")


def _frozen_explanation_self_assessment_is_idempotent(frozen):
    client, transaction = frozen
    payload = {'attempt_id': 'original-explanation', 'revision': 1, 'context': 'practice', 'answer': {'type': 'explanation', 'text': 'answer'}}
    first = client.post(BASE + 'explain/attempts', json=payload, headers={'Idempotency-Key': 'original-parent'})
    assert first.json()['pending_self_assessment'] is True
    assert client.post(BASE + 'explain/attempts', json=payload, headers={'Idempotency-Key': 'original-parent'}).json() == first.json()
    result = client.post(BASE + 'explain/attempts/original-explanation/self-assess', json={'rating': 'again'}, headers={'Idempotency-Key': 'original-child'})
    assert result.json()['persisted'] is True and result.json()['review'] is None
    assert client.post(BASE + 'explain/attempts/original-explanation/self-assess', json={'rating': 'again'}, headers={'Idempotency-Key': 'original-child'}).json() == result.json()
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM study_attempts WHERE id='original-explanation'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM operation_receipts WHERE operation_id IN ('original-parent','original-child') AND state='complete'").fetchone()[0] == 2




def _frozen_practice_has_no_queue_or_review_effect(frozen):
    client, transaction = frozen
    body = {'attempt_id': 'practice-hint', 'revision': 1, 'context': 'practice', 'expected_review_id': 91,
            'answer': {'type': 'choice', 'option_ids': ['a']}}
    response = client.post(BASE + 'choice/attempts', json=body, headers={'Idempotency-Key': 'practice-hint-operation'})
    assert response.json()['persisted'] is True and response.json()['review'] is None
    for field, value in (('card_id', 'card-choice'), ('queue_entry_id', 99), ('queue_cycle', 0)):
        invalid = {**body, 'attempt_id': 'invalid-' + field, field: value}
        client.post(BASE + 'choice/attempts', json=invalid, headers={'Idempotency-Key': 'invalid-operation-' + field})
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM reviews WHERE source_ref='practice-hint'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM study_attempts WHERE id LIKE %s", ("invalid-%",)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM operation_receipts WHERE operation_id LIKE %s AND state='failed'", ("invalid-operation-%",)).fetchone()[0] == 3


def test_frozen_study_native_attempts_and_recovery(frozen, monkeypatch):
    _frozen_queued_attempt_receipt_and_lost_ack_replay(frozen, 'a', 'correct')
    _frozen_queued_attempt_receipt_and_lost_ack_replay(frozen, 'b', 'again')
    _frozen_explanation_self_assessment_is_idempotent(frozen)
    _frozen_practice_has_no_queue_or_review_effect(frozen)
    _issued_before_archive_remounts_and_saves_original_attempt(frozen, monkeypatch)


def _issued_before_archive_remounts_and_saves_original_attempt(frozen, monkeypatch):
    from tempo_storage.http_composition import queue_projection
    from tempo_storage import connections
    from app.product_composition import study as composition
    client, transaction = frozen
    day = date.today().isoformat()
    with transaction() as db:
        for name in ('retained', 'unissued'):
            db.execute("INSERT INTO study_exercises(id,study_id,position_id,current_revision,status,created_at,updated_at) VALUES(%s,'frozen','position',1,'published','2026-01-01','2026-01-01')", (name,))
            db.execute("INSERT INTO study_exercise_revisions(exercise_id,revision,specification_json,specification_hash,created_at) VALUES(%s,1,%s,'original','2026-01-01')", (name, json.dumps(SPEC)))
            db.execute("INSERT INTO cards(id,repertoire_id,kind,content_type,study_exercise_id,start_fen,moves_json,state,due_date,revision) VALUES(%s,NULL,'exercise','study_exercise',%s,%s,'[]','learning',%s,1)", ('card-' + name, name, FEN, day))
        retained = db.execute("INSERT INTO daily_queue(queue_date,card_id,position,cycle) VALUES(%s,'card-retained',-1000,0) RETURNING id", (day,)).fetchone()[0]
        unissued = db.execute("INSERT INTO daily_queue(queue_date,card_id,position,cycle) VALUES(%s,'card-unissued',-999,0) RETURNING id", (day,)).fetchone()[0]
    # Real issuer stamps the original queue/card/revision; no synthetic origin.
    issued = command_gateway.execute_command('original-issued-head', 'queue.issue_head',
        {'queue_date': day, 'entry_id': retained, 'card_id': 'card-retained', 'expected_revision': 1})
    if issued is None:
        with transaction() as db:
            receipt = db.execute("SELECT state,error_json,last_error_json FROM operation_receipts WHERE operation_id=%s", ('original-issued-head',)).fetchone()
            raise AssertionError(f"Original issuance failed; durable receipt: {dict(receipt) if receipt else None}")
    assert issued['issued'] is True
    parent = {'attempt_id': 'pending-practice', 'revision': 1, 'context': 'practice', 'answer': {'type': 'explanation', 'text': 'original'}}
    assert client.post(BASE + 'explain/attempts', json=parent, headers={'Idempotency-Key': 'pending-practice-parent'}).json()['pending_self_assessment'] is True
    with transaction() as db:
        origin = dict(db.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id='card-retained' AND revision=1", (retained,)).fetchone())
        assert origin['issued_as_head'] == 1
        db.execute("UPDATE studies SET archived=1 WHERE id='frozen'")
        db.execute("UPDATE study_exercises SET status='archived' WHERE id IN ('retained','unissued','explain')")
    # A reload must find the issued row first, without admitting the unissued
    # archive or dispatching another issuance; exact origin survives unchanged.
    with transaction() as db:
        rows = queue_projection.queue_rows(db, day, 20, miss_reason='')
        assert rows and rows[0]['queue_entry_id'] == retained
        assert all(row['queue_entry_id'] != unissued for row in rows)
    monkeypatch.setattr(connections, 'read_connection', transaction)
    def no_dispatch(*args, **kwargs):
        raise AssertionError('reload must replay the issued head, not dispatch another issuance')
    monkeypatch.setattr(command_dispatch, 'dispatch_command', no_dispatch)
    outcome, result, transport = composition._issue_facts(day, {'queue_entry_id': retained, 'id': 'card-retained', 'revision': 1},
        read_connection=transaction, queue_projection_storage=queue_projection)
    assert outcome is None and transport is None and result == {'issued': True}
    with transaction() as db:
        assert dict(db.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id='card-retained' AND revision=1", (retained,)).fetchone()) == origin
    # Restore the fixture-only gateway transport for the original save.
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda name, payload, *, idempotency_key: command_gateway.execute_command(idempotency_key, name, payload))
    assert client.get(BASE + 'retained/present').status_code == 404
    query = f'?card_id=card-retained&queue_entry_id={retained}&queue_cycle=0&revision=1'
    assert client.get(BASE + 'retained/present' + query).status_code == 200
    assert client.get(BASE + f'unissued/present?card_id=card-unissued&queue_entry_id={unissued}&queue_cycle=0&revision=1').status_code == 404
    assert client.get(BASE + 'explain/present?attempt_id=pending-practice').status_code == 200
    assert client.get(BASE + 'explain/present?attempt_id=other').status_code == 404
    child = client.post(BASE + 'explain/attempts/pending-practice/self-assess', json={'rating': 'correct'}, headers={'Idempotency-Key': 'pending-practice-child'})
    assert child.json()['persisted'] is True and child.json()['review'] is None
    assert client.get(BASE + 'explain/present?attempt_id=pending-practice').status_code == 404
    body = {'attempt_id': 'issued-original', 'revision': 1, 'context': 'review', 'queue_entry_id': retained,
            'queue_cycle': 0, 'card_id': 'card-retained', 'answer': {'type': 'choice', 'option_ids': ['a']}}
    first = client.post(BASE + 'retained/attempts', json=body, headers={'Idempotency-Key': 'issued-original-operation'})
    assert first.json()['persisted'] is True
    assert client.post(BASE + 'retained/attempts', json=body, headers={'Idempotency-Key': 'issued-original-operation'}).json() == first.json()
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM study_attempts WHERE id='issued-original'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM reviews WHERE source_ref='issued-original'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM queue_attempt_origins WHERE queue_entry_id=%s AND issued_as_head=1", (retained,)).fetchone()[0] == 1
    for suffix, changed in (('unissued', {**body, 'attempt_id': 'wrong-origin', 'card_id': 'card-unissued', 'queue_entry_id': unissued}),
                            ('practice', {**parent, 'attempt_id': 'new-practice'})):
        client.post(BASE + ('unissued' if suffix == 'unissued' else 'explain') + '/attempts', json=changed, headers={'Idempotency-Key': 'reject-' + suffix})
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM operation_receipts WHERE operation_id IN ('reject-unissued','reject-practice') AND state='failed'").fetchone()[0] == 2
