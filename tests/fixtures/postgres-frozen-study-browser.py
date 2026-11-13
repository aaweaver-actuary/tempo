"""Invocation-owned frozen Study browser facts, never an issued plan or receipt."""
import hashlib
import json
import os
import re
import sys
from datetime import date
import psycopg

if os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable':
    raise RuntimeError('Owned disposable PostgreSQL is required')
action, prefix, day = sys.argv[1:4]
if not re.fullmatch(r'p12-frozen-[a-f0-9]{32}', prefix) or date.fromisoformat(day).isoformat() != day:
    raise ValueError('Invalid invocation identity or Study day')
fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
with psycopg.connect('postgresql://postgres@postgres:5432/tempo') as db:
    marker = db.execute("SELECT current_database(),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()
    if marker != ('tempo', 'tempo-disposable-postgres-test'):
        raise RuntimeError('Database is not owned disposable PostgreSQL')
    if action in ('seed', 'seed-requeue'):
        if db.execute('SELECT 1 FROM studies WHERE id=%s', (prefix,)).fetchone():
            raise RuntimeError('Fixture identity already exists')
        if db.execute("SELECT 1 FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id WHERE q.queue_date=%s AND q.status='queued' AND o.issued_as_head=1 LIMIT 1", (day,)).fetchone():
            raise RuntimeError('An existing issued attempt must not be displaced')
        if action == 'seed-requeue' and db.execute("SELECT 1 FROM daily_queue WHERE queue_date=%s AND status='queued' LIMIT 1", (day,)).fetchone():
            print(json.dumps({'ready': False})); sys.exit(0)
        projection = db.execute('SELECT state,refresh_pending FROM queue_projections WHERE queue_date=%s', (day,)).fetchone()
        if not projection or projection[0] != 'ready' or projection[1]:
            print(json.dumps({'ready': False}))
            sys.exit(0)
        prior = db.execute('SELECT id,position FROM daily_queue WHERE queue_date=%s ORDER BY position,id FOR UPDATE', (day,)).fetchall()
        shift = len(prior) + max((abs(position) for _,position in prior), default=0) + 10
        displaced = []
        for entry, position in prior:
            updated = position + shift
            db.execute('UPDATE daily_queue SET position=%s WHERE id=%s', (updated, entry))
            displaced.append([entry, position, updated])
        db.execute("INSERT INTO studies(id,title,created_at,updated_at) VALUES(%s,'P12 frozen Study','2000-01-01','2000-01-01')", (prefix,))
        db.execute("INSERT INTO study_chapters(id,study_id,title,position) VALUES(%s,%s,'Owned chapter',1)", (prefix+'-chapter',prefix))
        db.execute("INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,filename,record_index,headers_json,diagnostics_json,valid,created_at) VALUES(%s,%s,%s,1,'','p12','fixture',0,'{}','{}',1,'2000-01-01')", (prefix+'-source',prefix+'-chapter',prefix+'-group'))
        db.execute("INSERT INTO study_positions(id,source_id,child_index,fen,history_json,node_path,valid) VALUES(%s,%s,0,%s,'[]','root',1)", (prefix+'-position',prefix+'-source',fen))
        base = {'hint':'', 'explanation':'P12 original feedback'}
        definitions = [dict(base, type='move_line', prompt='P12 first move', mode='single', grading_policy='reference', accepted_lines=[['e2e4']]),
            dict(base, type='explanation', prompt='P12 explain the move', rubric='The pawn claims the centre.'),
            dict(base, type='move_line', prompt='P12 playable successor', mode='single', grading_policy='reference', accepted_lines=[['d2d4']])]
        if action == 'seed-requeue': definitions = definitions[:1]
        entries = []
        for index, specification in enumerate(definitions):
            exercise, card = f'{prefix}-exercise-{index}', f'{prefix}-card-{index}'
            encoded = json.dumps(specification, sort_keys=True, separators=(',', ':'))
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            db.execute("INSERT INTO study_exercises(id,study_id,position_id,current_revision,status,created_at,updated_at) VALUES(%s,%s,%s,1,'published','2000-01-01','2000-01-01')", (exercise,prefix,prefix+'-position'))
            db.execute("INSERT INTO study_exercise_revisions(exercise_id,revision,specification_json,specification_hash,created_at) VALUES(%s,1,%s,%s,'2000-01-01')", (exercise,encoded,digest))
            db.execute("INSERT INTO cards(id,repertoire_id,kind,content_type,study_exercise_id,start_fen,moves_json,state,due_date,revision,trained_color,introduced_at,first_correct_at) VALUES(%s,NULL,'exercise','study_exercise',%s,%s,'[]','learning',%s,1,'white','2000-01-01','2000-01-01T00:00:00+00:00')", (card,exercise,fen,day))
            entry = db.execute("INSERT INTO daily_queue(queue_date,card_id,position,cycle,admission_kind) VALUES(%s,%s,%s,0,'explicit') RETURNING id", (day,card,index)).fetchone()[0]
            entries.append(entry)
        if action == 'seed-requeue':
            # Retained old cycles are source facts; the real owner chooses the
            # next cycle after the genuine parent review is committed.
            db.execute("INSERT INTO daily_queue(queue_date,card_id,position,cycle,status) VALUES(%s,%s,9,3,'complete')", (day,prefix+'-card-0'))
        print(json.dumps({'ready':True, 'entries':entries, 'displaced':displaced}))
    elif action == 'snapshot':
        attempts = db.execute('SELECT id,exercise_id,revision,card_id,queue_entry_id,cycle,context,answer_hash,finalized_at,result_json FROM study_attempts WHERE exercise_id LIKE %s ORDER BY id', (prefix+'-exercise-%',)).fetchall()
        reviews = db.execute('SELECT id,card_id,rating FROM reviews WHERE card_id LIKE %s ORDER BY id', (prefix+'-card-%',)).fetchall()
        origins = db.execute('SELECT queue_entry_id,card_id,revision,issued_as_head FROM queue_attempt_origins WHERE card_id LIKE %s ORDER BY queue_entry_id', (prefix+'-card-%',)).fetchall()
        print(json.dumps({'attempts':attempts, 'reviews':reviews, 'origins':origins},default=str))
    elif action == 'cleanup':
        displaced, operations = json.loads(sys.argv[4]), json.loads(sys.argv[5])
        if not isinstance(operations, list) or any(not isinstance(value, str) or not value for value in operations):
            raise RuntimeError('Invalid original operation identities')
        owner_cards = [f'{prefix}-card-{i}' for i in range(3)]
        owned = db.execute("""SELECT operation_id,state FROM operation_receipts WHERE
          (command_name IN ('studies.attempts.submit','studies.attempts.self_assess') AND payload_json::jsonb->>'study_id'=%s)
          OR (command_name='queue.issue_head' AND payload_json::jsonb->>'card_id' = ANY(%s))""", (prefix, owner_cards)).fetchall()
        owned_ids = {row[0] for row in owned}
        if any(item not in owned_ids for item in operations) or any(row[1] not in ('complete','failed') for row in owned):
            raise RuntimeError('Original operations are missing, foreign or unfinished; preserve fixture')
        db.execute('DELETE FROM operation_receipts WHERE operation_id = ANY(%s)', (list(owned_ids),))
        db.execute('DELETE FROM study_attempts WHERE exercise_id LIKE %s', (prefix+'-exercise-%',))
        db.execute('DELETE FROM study_sibling_burials WHERE exercise_id LIKE %s', (prefix+'-exercise-%',))
        db.execute('DELETE FROM review_schedule_snapshots WHERE review_id IN (SELECT id FROM reviews WHERE card_id LIKE %s)', (prefix+'-card-%',))
        db.execute('DELETE FROM review_attempt_receipts WHERE card_id LIKE %s', (prefix+'-card-%',))
        db.execute('DELETE FROM reviews WHERE card_id LIKE %s', (prefix+'-card-%',))
        # Queue deletion triggers retained-origin capture; remove origins only after it.
        db.execute('DELETE FROM daily_queue WHERE card_id LIKE %s', (prefix+'-card-%',))
        db.execute('DELETE FROM queue_attempt_origins WHERE card_id LIKE %s', (prefix+'-card-%',))
        db.execute('DELETE FROM cards WHERE id LIKE %s', (prefix+'-card-%',))
        db.execute('DELETE FROM study_exercise_revisions WHERE exercise_id LIKE %s', (prefix+'-exercise-%',))
        db.execute('DELETE FROM study_exercises WHERE study_id=%s', (prefix,))
        db.execute('DELETE FROM study_positions WHERE source_id=%s', (prefix+'-source',))
        db.execute('DELETE FROM study_sources WHERE chapter_id=%s', (prefix+'-chapter',))
        db.execute('DELETE FROM study_chapters WHERE study_id=%s', (prefix,))
        db.execute('DELETE FROM studies WHERE id=%s', (prefix,))
        restored = reordered = removed = 0
        for entry, original, expected in displaced:
            row = db.execute('SELECT position FROM daily_queue WHERE id=%s FOR UPDATE', (entry,)).fetchone()
            if row is None: removed += 1
            elif row[0] != expected: reordered += 1
            else:
                restored += db.execute('UPDATE daily_queue SET position=%s WHERE id=%s AND position=%s', (original,entry,expected)).rowcount
        remaining = {}
        for table, column, value in [('studies','id',prefix), ('study_chapters','study_id',prefix),
          ('study_exercises','study_id',prefix), ('study_attempts','exercise_id',prefix+'-exercise-%'),
          ('cards','id',prefix+'-card-%'), ('reviews','card_id',prefix+'-card-%'),
          ('daily_queue','card_id',prefix+'-card-%'), ('queue_attempt_origins','card_id',prefix+'-card-%'),
          ('review_attempt_receipts','card_id',prefix+'-card-%')]:
            op = 'LIKE' if value.endswith('%') else '='
            remaining[table] = db.execute(f'SELECT count(*) FROM {table} WHERE {column} {op} %s', (value,)).fetchone()[0]
        remaining['operations'] = db.execute('SELECT count(*) FROM operation_receipts WHERE operation_id = ANY(%s)', (list(owned_ids),)).fetchone()[0]
        if any(remaining.values()) or restored + reordered + removed != len(displaced):
            raise RuntimeError(f'Owned frozen fixture cleanup incomplete: {remaining}')
        print(json.dumps({'cleaned':True, 'remaining':remaining, 'restored':restored, 'reordered':reordered, 'removed':removed}))
    else:
        raise ValueError('Unknown fixture action')
