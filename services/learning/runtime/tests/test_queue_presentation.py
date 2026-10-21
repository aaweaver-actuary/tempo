"""Actual independent-runtime adapter for the shared presentation policy."""
from collections import Counter
from tempo_learning_database.connections import connection
from tempo_learning_runtime.maintenance import run_once
from tempo_learning_runtime.queue_application import request_queue_refresh_in_transaction
from tempo_learning_runtime.queue_ordering import PRESENTATION_VERSION
from test_runtime import activate, bundle, head, review_payload, submit


def test_runtime_balances_admitted_work_and_replays_without_moving_issued_head(case):
    import chess
    moves = [move.uci() for move in chess.Board().legal_moves][:10]
    payload = bundle([(kind,dict(type='move_line',accepted_lines=[[move]],mode='single',grading_policy='reference'))
                      for kind,move in zip(['opening']*9+['tactic'],moves)])
    activate(case,payload)
    rows = [tuple(row) for row in case.query("SELECT q.id,q.card_id,q.position,c.content_type,q.queue_date FROM learning.daily_queue q JOIN learning.cards c ON c.id=q.card_id WHERE q.status='queued' ORDER BY position,id")]
    assert len(rows)==10
    assert 3 <= next(index+1 for index,row in enumerate(rows) if row[3]=='tactic') <= 8
    assert Counter(row[3] for row in rows)=={'opening':9,'tactic':1}
    issued = head(case)
    origins = [tuple(row) for row in case.query("SELECT queue_entry_id,card_id,admission_kind,admission_repertoire_id FROM learning.queue_attempt_origins ORDER BY queue_entry_id")]
    with case.database.scope():
        with connection() as database:
            request_queue_refresh_in_transaction(database,rows[0][4])
        for _ in range(30):
            if not run_once():
                break
        else:
            raise AssertionError('Finite refresh did not finish')
    assert [tuple(row) for row in case.query("SELECT q.id,q.card_id,q.position,c.content_type,q.queue_date FROM learning.daily_queue q JOIN learning.cards c ON c.id=q.card_id WHERE q.status='queued' ORDER BY position,id")] == rows
    assert [tuple(row) for row in case.query("SELECT queue_entry_id,card_id,admission_kind,admission_repertoire_id FROM learning.queue_attempt_origins ORDER BY queue_entry_id")] == origins
    assert case.query("SELECT membership_hash FROM learning.daily_queue_days")[0][0].startswith(PRESENTATION_VERSION+':')
    assert case.query("SELECT position FROM learning.daily_queue WHERE id=%s",(issued['queue_entry_id'],))[0][0] == rows[0][2]
    # A real failed review completes the issued head and inserts its positioned
    # repeat. Its verified receipt must survive the subsequent refresh intact.
    import uuid
    operation = uuid.uuid4().hex
    payload = review_payload(issued, outcome='again')
    response = submit(case, issued, payload, operation=operation)
    assert response.status_code == 200, response.text
    assert submit(case, issued, payload, operation=operation).json() == response.json()
    assert case.query("SELECT membership_hash FROM learning.daily_queue_days")[0][0].startswith(PRESENTATION_VERSION+':')
    positioned = [tuple(row) for row in case.query("SELECT id,position FROM learning.daily_queue WHERE status='queued' ORDER BY position,id")]
    with case.database.scope():
        for _ in range(30):
            if not run_once(): break
        else: raise AssertionError('Finite post-review refresh did not finish')
    assert [tuple(row) for row in case.query("SELECT id,position FROM learning.daily_queue WHERE status='queued' ORDER BY position,id")] == positioned
    assert case.query('SELECT COUNT(*) FROM learning.reviews')[0][0] == 1
