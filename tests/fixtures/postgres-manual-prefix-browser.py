"""Owned disposable fixture facts for the real manual prefix-split browser case.

Only the initial prior-failure history and queue ordering are synthetic. The
application must issue the head, accept the split, save the review and recover
its own child operation. This helper never creates an issued origin or receipt.
"""
import json
import os
import re
import sys
import uuid
from datetime import date

import psycopg

if os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable':
    raise RuntimeError('Owned disposable PostgreSQL is required')
action, fixture_name, repertoire, card, day = sys.argv[1:6]
if not re.fullmatch(r'p12-split-[a-f0-9]{32}\.pgn', fixture_name):
    raise ValueError('Expected this invocation\'s synthetic PGN name')
if not re.fullmatch(r'[a-zA-Z0-9_-]+', repertoire) or not re.fullmatch(r'[a-zA-Z0-9_-]+', card):
    raise ValueError('Invalid original fixture identity')
if date.fromisoformat(day).isoformat() != day:
    raise ValueError('Invalid original Study day')

with psycopg.connect('postgresql://postgres@postgres:5432/tempo') as db:
    if action == 'wake-snapshot':
        db.execute('SET TRANSACTION READ ONLY')
    marker = db.execute("SELECT current_database(), shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()
    if marker != ('tempo', 'tempo-disposable-postgres-test'):
        raise RuntimeError('Database is not the owned disposable runner')
    if action == 'prepare' and card == 'auto':
        if not db.execute('SELECT 1 FROM repertoires WHERE id=%s AND source_name=%s', (repertoire, fixture_name)).fetchone():
            raise RuntimeError('The imported repertoire does not match this invocation')
        candidates = db.execute("SELECT c.id FROM cards c JOIN repertoire_cards rc ON rc.card_id=c.id JOIN daily_queue q ON q.card_id=c.id WHERE rc.repertoire_id=%s AND q.queue_date=%s AND q.status='queued' AND c.kind='prefix' AND json_array_length(c.moves_json)>2 ORDER BY q.position,q.id", (repertoire, day)).fetchall()
        if not candidates:
            print(json.dumps({'ready': False}))
            sys.exit(0)
        card = candidates[0][0]
    if action == 'cleanup' and card == 'auto':
        if not db.execute('SELECT 1 FROM repertoires WHERE id=%s AND source_name=%s', (repertoire, fixture_name)).fetchone():
            raise RuntimeError('Original imported repertoire is unavailable')
        first = db.execute('SELECT id FROM cards WHERE repertoire_id=%s ORDER BY id LIMIT 1', (repertoire,)).fetchone()
        card = first[0] if first else 'auto'
    if card != 'auto' and not db.execute('SELECT 1 FROM repertoires r WHERE r.id=%s AND r.source_name=%s AND (EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.repertoire_id=r.id AND rc.card_id=%s) OR EXISTS(SELECT 1 FROM prefix_splits ps JOIN repertoire_cards rc ON rc.card_id=ps.shortened_card_id WHERE ps.source_card_id=%s AND rc.repertoire_id=r.id))', (repertoire, fixture_name, card, card)).fetchone():
        raise RuntimeError('The original imported card and repertoire do not match this invocation')
    if action == 'prepare':
        shared = db.execute('SELECT 1 FROM repertoire_cards a JOIN repertoire_cards b ON b.card_id=a.card_id AND b.repertoire_id<>a.repertoire_id WHERE a.repertoire_id=%s LIMIT 1', (repertoire,)).fetchone()
        foreign = db.execute('SELECT 1 FROM repertoire_cards rc JOIN cards c ON c.id=rc.card_id WHERE rc.repertoire_id=%s AND c.repertoire_id IS DISTINCT FROM %s LIMIT 1', (repertoire,repertoire)).fetchone()
        if shared or foreign:
            raise RuntimeError('Fixture repertoire shares card ownership; preserve it')
        if db.execute('SELECT 1 FROM queue_attempt_origins WHERE card_id=%s AND issued_as_head=1', (card,)).fetchone():
            raise RuntimeError('Fixture cannot alter an already issued attempt')
        if db.execute("SELECT 1 FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id WHERE q.queue_date=%s AND q.status='queued' AND o.issued_as_head=1 LIMIT 1", (day,)).fetchone():
            raise RuntimeError('An existing issued attempt must not be displaced')
        q = db.execute("SELECT id FROM daily_queue WHERE queue_date=%s AND card_id=%s AND status='queued'", (day, card)).fetchall()
        if len(q) != 1:
            raise RuntimeError('Expected exactly one unissued original queue entry')
        entry = q[0][0]
        existing = db.execute('SELECT id,position FROM daily_queue WHERE queue_date=%s ORDER BY position,id FOR UPDATE', (day,)).fetchall()
        shift = 2 + max((abs(position) for _, position in existing), default=0)
        displaced = []
        for entry_id, position in existing:
            new_position = 0 if entry_id == entry else position + shift
            db.execute('UPDATE daily_queue SET position=%s WHERE id=%s', (new_position, entry_id))
            if entry_id != entry:
                displaced.append([entry_id, position, new_position])
        db.execute('UPDATE cards SET recent_attempts_json=%s WHERE id=%s', ('["again","again","again"]', card))
        prior = db.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(%s,'again','2000-01-01T00:00:00Z',1,1,'study') RETURNING id", (card,)).fetchone()[0]
        print(json.dumps({'ready': True, 'card': card, 'entry': entry, 'prior_review': prior, 'displaced': displaced}))
    elif action == 'wake-snapshot':
        acceptance, review, intent = sys.argv[6:9]
        if str(uuid.UUID(acceptance)) != acceptance or not review.startswith('review-attempt:') or str(uuid.UUID(review.removeprefix('review-attempt:'))) != review.removeprefix('review-attempt:') or str(uuid.UUID(intent)) != intent:
            raise RuntimeError('Invalid exact wake identity')
        child = 'prefix-split-intent:' + intent
        owned = db.execute('SELECT intent_id,card_id,source_revision,queue_entry_id,state,review_attempt_id,review_id,child_operation_id FROM study_deferred_prefix_splits WHERE intent_id=%s AND card_id=%s', (intent, card)).fetchone()
        operations = db.execute("SELECT operation_id,command_name,state,created_at,updated_at,attempt_count,background,request_hash,response_json FROM operation_receipts WHERE operation_id=ANY(%s) ORDER BY operation_id", ([acceptance,review,child],)).fetchall()
        receipts = db.execute('SELECT attempt_id,card_id,queue_entry_id,review_id,result_json FROM review_attempt_receipts WHERE attempt_id=%s AND card_id=%s', (review.removeprefix('review-attempt:'), card)).fetchall()
        split = db.execute('SELECT source_card_id,source_revision,shortened_card_id,continuation_card_id FROM prefix_splits WHERE source_card_id=%s', (card,)).fetchall()
        print(json.dumps({'intent':owned,'operations':operations,'receipts':receipts,'split':split}, default=str))
    elif action == 'snapshot':
        split = db.execute('SELECT source_card_id,source_revision,shortened_card_id,continuation_card_id FROM prefix_splits WHERE source_card_id=%s', (card,)).fetchall()
        intent = db.execute('SELECT intent_id,card_id,source_revision,queue_entry_id,state,review_attempt_id,review_id,child_operation_id,result_json FROM study_deferred_prefix_splits WHERE card_id=%s ORDER BY intent_id', (card,)).fetchall()
        reviews = db.execute('SELECT id,rating FROM reviews WHERE card_id=%s ORDER BY id', (card,)).fetchall()
        receipts = db.execute('SELECT attempt_id,queue_entry_id,review_id,result_json FROM review_attempt_receipts WHERE card_id=%s ORDER BY attempt_id', (card,)).fetchall()
        origin = db.execute('SELECT queue_entry_id,card_id,revision,issued_as_head FROM queue_attempt_origins WHERE card_id=%s ORDER BY queue_entry_id', (card,)).fetchall()
        queue = db.execute('SELECT q.id,q.card_id,q.cycle,q.status FROM daily_queue q WHERE q.card_id=%s OR q.card_id IN (SELECT shortened_card_id FROM prefix_splits WHERE source_card_id=%s UNION SELECT continuation_card_id FROM prefix_splits WHERE source_card_id=%s) ORDER BY q.id', (card, card, card)).fetchall()
        print(json.dumps({'split': split, 'intent': intent, 'reviews': reviews, 'receipts': receipts, 'origins': origin, 'queue': queue}, default=str))
    elif action == 'cleanup':
        displaced, tracked = json.loads(sys.argv[6]), json.loads(sys.argv[7])
        if not isinstance(tracked, list) or any(not isinstance(value,str) or not value for value in tracked):
            raise RuntimeError('Invalid original operation identities')
        owned_cards = [row[0] for row in db.execute('SELECT id FROM cards WHERE repertoire_id=%s ORDER BY id', (repertoire,)).fetchall()]
        if (card != 'auto' and card not in owned_cards) or db.execute('SELECT 1 FROM repertoire_cards WHERE card_id = ANY(%s) AND repertoire_id<>%s LIMIT 1', (owned_cards,repertoire)).fetchone():
            raise RuntimeError('Fixture ownership changed or became shared; preserve it')
        intent_ids = [row[0] for row in db.execute('SELECT intent_id FROM study_deferred_prefix_splits WHERE card_id = ANY(%s)', (owned_cards,)).fetchall()]
        owner_operations = db.execute("""SELECT operation_id,state FROM operation_receipts WHERE
          (command_name IN ('cards.prefix_split.accept','cards.prefix_split.reject','cards.review','cards.review.reconcile','queue.issue_head')
           AND payload_json::jsonb->>'card_id' = ANY(%s))
          OR (command_name='cards.prefix_split.apply_deferred' AND payload_json::jsonb->>'intent_id' = ANY(%s))
          OR (command_name='imports.pgn.explicit' AND payload_json::jsonb->>'source_name'=%s)
          OR (command_name='repertoires.settings.update' AND payload_json::jsonb->>'repertoire_id'=%s)""", (owned_cards,intent_ids,fixture_name,repertoire)).fetchall()
        operation_ids = {row[0] for row in owner_operations}
        if any(value not in operation_ids for value in tracked) or any(row[1] not in ('complete','failed') for row in owner_operations):
            raise RuntimeError('Original operations are missing, foreign or unfinished; preserve fixture')
        review_ids = [row[0] for row in db.execute('SELECT id FROM reviews WHERE card_id = ANY(%s)', (owned_cards,)).fetchall()]
        # Deletion is routed through the existing owner on the verified disposable
        # database. Retained test-only origins and receipts are then cleared by
        # exact captured identities, after queue-delete triggers have run.
        from tempo_storage.postgres import PostgresConnection, tempo_row_factory
        from app.card_deletion import delete_repertoire_data
        old_factory = db.row_factory
        db.row_factory = tempo_row_factory
        try:
            result = delete_repertoire_data(PostgresConnection(db), repertoire, 'delete')
        finally:
            db.row_factory = old_factory
        if result != {'deleted': True, 'id': repertoire}:
            raise RuntimeError('Fixture repertoire deletion did not complete')
        db.execute('DELETE FROM study_deferred_prefix_splits WHERE intent_id = ANY(%s)', (intent_ids,))
        db.execute('DELETE FROM operation_receipts WHERE operation_id = ANY(%s)', (list(operation_ids),))
        db.execute('DELETE FROM queue_attempt_origins WHERE card_id = ANY(%s)', (owned_cards,))
        db.execute('DELETE FROM review_attempt_receipts WHERE card_id = ANY(%s)', (owned_cards,))
        db.execute('DELETE FROM review_schedule_snapshots WHERE review_id = ANY(%s)', (review_ids,))
        db.execute('DELETE FROM deleted_cards WHERE card_id = ANY(%s)', (owned_cards,))
        restored = reordered = removed = 0
        for entry_id, original, expected in displaced:
            row = db.execute('SELECT position FROM daily_queue WHERE id=%s FOR UPDATE', (entry_id,)).fetchone()
            if row is None: removed += 1
            elif row[0] != expected: reordered += 1
            else:
                restored += db.execute('UPDATE daily_queue SET position=%s WHERE id=%s AND position=%s', (original,entry_id,expected)).rowcount
        remaining = {}
        for table, column, values in [('cards','id',owned_cards), ('reviews','card_id',owned_cards),
          ('daily_queue','card_id',owned_cards), ('queue_attempt_origins','card_id',owned_cards),
          ('review_attempt_receipts','card_id',owned_cards), ('deleted_cards','card_id',owned_cards),
          ('study_deferred_prefix_splits','intent_id',intent_ids), ('operation_receipts','operation_id',list(operation_ids))]:
            remaining[table] = db.execute(f'SELECT count(*) FROM {table} WHERE {column} = ANY(%s)', (values,)).fetchone()[0]
        remaining['repertoire'] = db.execute('SELECT count(*) FROM repertoires WHERE id=%s', (repertoire,)).fetchone()[0]
        remaining['schedule_snapshots'] = db.execute('SELECT count(*) FROM review_schedule_snapshots WHERE review_id = ANY(%s)', (review_ids,)).fetchone()[0]
        if any(remaining.values()) or restored + reordered + removed != len(displaced):
            raise RuntimeError(f'Owned manual split fixture cleanup incomplete: {remaining}')
        print(json.dumps({'cleaned':True, 'remaining':remaining, 'restored':restored, 'reordered':reordered, 'removed':removed}))
    else:
        raise ValueError('Unknown fixture action')
