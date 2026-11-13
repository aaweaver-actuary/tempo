"""Owned disposable fixture facts for the real manual prefix-split browser case.

Only the initial prior-failure history and queue ordering are synthetic. The
application must issue the head, accept the split, save the review and recover
its own child operation. This helper never creates an issued origin or receipt.
"""
import json
import os
import re
import sys
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
    if not db.execute('SELECT 1 FROM repertoires r WHERE r.id=%s AND r.source_name=%s AND (EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.repertoire_id=r.id AND rc.card_id=%s) OR EXISTS(SELECT 1 FROM prefix_splits ps JOIN repertoire_cards rc ON rc.card_id=ps.shortened_card_id WHERE ps.source_card_id=%s AND rc.repertoire_id=r.id))', (repertoire, fixture_name, card, card)).fetchone():
        raise RuntimeError('The original imported card and repertoire do not match this invocation')
    if action == 'prepare':
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
    elif action == 'snapshot':
        split = db.execute('SELECT source_card_id,source_revision,shortened_card_id,continuation_card_id FROM prefix_splits WHERE source_card_id=%s', (card,)).fetchall()
        intent = db.execute('SELECT intent_id,card_id,source_revision,queue_entry_id,state,review_attempt_id,review_id,child_operation_id,result_json FROM study_deferred_prefix_splits WHERE card_id=%s ORDER BY intent_id', (card,)).fetchall()
        reviews = db.execute('SELECT id,rating FROM reviews WHERE card_id=%s ORDER BY id', (card,)).fetchall()
        receipts = db.execute('SELECT attempt_id,queue_entry_id,review_id,result_json FROM review_attempt_receipts WHERE card_id=%s ORDER BY attempt_id', (card,)).fetchall()
        origin = db.execute('SELECT queue_entry_id,card_id,revision,issued_as_head FROM queue_attempt_origins WHERE card_id=%s ORDER BY queue_entry_id', (card,)).fetchall()
        queue = db.execute('SELECT q.id,q.card_id,q.cycle,q.status FROM daily_queue q WHERE q.card_id=%s OR q.card_id IN (SELECT shortened_card_id FROM prefix_splits WHERE source_card_id=%s UNION SELECT continuation_card_id FROM prefix_splits WHERE source_card_id=%s) ORDER BY q.id', (card, card, card)).fetchall()
        print(json.dumps({'split': split, 'intent': intent, 'reviews': reviews, 'receipts': receipts, 'origins': origin, 'queue': queue}, default=str))
    elif action == 'restore':
        displaced = json.loads(sys.argv[6])
        for entry_id, original, expected in displaced:
            db.execute('UPDATE daily_queue SET position=%s WHERE id=%s AND position=%s', (original, entry_id, expected))
        print(json.dumps({'restored': True}))
    else:
        raise ValueError('Unknown fixture action')
